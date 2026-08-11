"""Point-in-time CSI 300 factor backtest for the Weixin stock assistant.

The research path is deliberately separate from the live bot. It obtains
historical CSI 300 membership and daily bars from BaoStock, caches the raw data
in SQLite, and executes monthly signals at the next trading day's open.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


PRICE_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,turn,"
    "tradestatus,pctChg,peTTM,pbMRQ,isST"
)
BENCHMARK_CODE = "sh.000300"
COMMISSION_RATE = 0.0003
SLIPPAGE_RATE = 0.0005
MIN_COMMISSION = 5.0
STAMP_TAX_CUTOVER = pd.Timestamp("2023-08-28")


STRATEGIES: dict[str, dict[str, float]] = {
    # Price/valuation proxy for the factors currently used by the live bot.
    "live_proxy": {
        "trend": 0.3125,
        "momentum_live": 0.25,
        "value_balanced": 0.1875,
        "low_volatility": 0.125,
        "liquidity": 0.0625,
        "risk_control": 0.0625,
    },
    # Momentum excludes the most recent month to reduce short-term reversal.
    "balanced_6_1": {
        "trend": 0.30,
        "momentum_6_1": 0.30,
        "value_balanced": 0.15,
        "low_volatility": 0.15,
        "liquidity": 0.05,
        "risk_control": 0.05,
    },
    "momentum_defensive": {
        "trend": 0.25,
        "momentum_6_1": 0.35,
        "value_balanced": 0.10,
        "low_volatility": 0.20,
        "risk_control": 0.10,
    },
    "trend_value": {
        "trend": 0.35,
        "momentum_6_1": 0.20,
        "value_balanced": 0.20,
        "low_volatility": 0.10,
        "liquidity": 0.05,
        "risk_control": 0.10,
    },
    "low_vol_value": {
        "trend": 0.20,
        "momentum_6_1": 0.20,
        "value_cheap": 0.20,
        "low_volatility": 0.25,
        "liquidity": 0.05,
        "risk_control": 0.10,
    },
    # Exact price/valuation portion of the conservative v0.9.0 live blend.
    # Historical quality data is intentionally omitted instead of backfilled.
    "deployed_blend": {
        "trend": 0.225,
        "momentum_6_1": 0.20,
        "value_cheap": 0.175,
        "low_volatility": 0.175,
        "liquidity": 0.05,
        "risk_control": 0.075,
    },
}

# Conservative blend deployed to the live bot after reviewing holdout results.
LIVE_DEPLOYMENT_WEIGHTS = {
    "trend": 0.225,
    "momentum_6_1": 0.20,
    "quality": 0.10,
    "value": 0.175,
    "low_volatility": 0.175,
    "liquidity": 0.05,
    "risk_control": 0.075,
}


@dataclass(frozen=True)
class Variant:
    strategy: str
    topk: int
    regime_filter: bool
    sector_cap: bool = False

    @property
    def key(self) -> str:
        suffix = "regime" if self.regime_filter else "always"
        sector = "sector-cap" if self.sector_cap else "uncapped"
        return f"{self.strategy}:top{self.topk}:{suffix}:{sector}"


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _month_ends(days: Iterable[str]) -> list[str]:
    grouped: dict[str, str] = {}
    for raw in days:
        stamp = str(raw)
        grouped[stamp[:7]] = stamp
    return list(grouped.values())


def _query_rows(result: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise RuntimeError(result.error_msg)
    return rows


class BacktestCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS trading_days(
              date TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS memberships(
              signal_date TEXT NOT NULL,
              source_date TEXT NOT NULL,
              code TEXT NOT NULL,
              name TEXT NOT NULL,
              PRIMARY KEY(signal_date, code)
            );
            CREATE TABLE IF NOT EXISTS industries(
              signal_date TEXT NOT NULL,
              source_date TEXT NOT NULL,
              code TEXT NOT NULL,
              industry TEXT NOT NULL,
              PRIMARY KEY(signal_date, code)
            );
            CREATE TABLE IF NOT EXISTS prices(
              code TEXT NOT NULL,
              date TEXT NOT NULL,
              open REAL, high REAL, low REAL, close REAL, preclose REAL,
              volume REAL, amount REAL, turn REAL, tradestatus INTEGER,
              pct_chg REAL, pe_ttm REAL, pb_mrq REAL, is_st INTEGER,
              PRIMARY KEY(code, date)
            );
            CREATE TABLE IF NOT EXISTS metadata(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date);
            CREATE INDEX IF NOT EXISTS idx_memberships_date ON memberships(signal_date);
            CREATE INDEX IF NOT EXISTS idx_industries_date ON industries(signal_date);
            """
        )

    def close(self) -> None:
        self.db.close()

    def trading_days(self, start: str, end: str) -> list[str]:
        return [
            row[0] for row in self.db.execute(
                "SELECT date FROM trading_days WHERE date BETWEEN ? AND ? ORDER BY date",
                (start, end),
            )
        ]

    def membership_dates(self, start: str, end: str) -> list[str]:
        return [
            row[0] for row in self.db.execute(
                "SELECT DISTINCT signal_date FROM memberships "
                "WHERE signal_date BETWEEN ? AND ? ORDER BY signal_date",
                (start, end),
            )
        ]

    def members(self, signal_date: str) -> list[str]:
        return [
            row[0] for row in self.db.execute(
                "SELECT code FROM memberships WHERE signal_date=? ORDER BY code",
                (signal_date,),
            )
        ]

    def industries(self, signal_date: str) -> dict[str, str]:
        return dict(self.db.execute(
            "SELECT code,industry FROM industries WHERE signal_date=?",
            (signal_date,),
        ))

    def price_coverage(self, code: str) -> tuple[str | None, str | None, int]:
        row = self.db.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM prices WHERE code=?",
            (code,),
        ).fetchone()
        return row[0], row[1], int(row[2] or 0)

    def upsert_price_rows(self, rows: list[list[str]]) -> None:
        payload = []
        for row in rows:
            if len(row) != 15:
                continue
            payload.append((
                row[1], row[0],
                _float(row[2]), _float(row[3]), _float(row[4]), _float(row[5]),
                _float(row[6]), _float(row[7]), _float(row[8]), _float(row[9]),
                int(_float(row[10]) or 0), _float(row[11]), _float(row[12]),
                _float(row[13]), int(_float(row[14]) or 0),
            ))
        self.db.executemany(
            """
            INSERT OR REPLACE INTO prices(
              code,date,open,high,low,close,preclose,volume,amount,turn,
              tradestatus,pct_chg,pe_ttm,pb_mrq,is_st
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            payload,
        )

    def load_prices(self, start: str, end: str) -> pd.DataFrame:
        frame = pd.read_sql_query(
            "SELECT * FROM prices WHERE date BETWEEN ? AND ? ORDER BY code,date",
            self.db,
            params=(start, end),
        )
        if frame.empty:
            raise RuntimeError("回测缓存没有价格数据，请先运行 fetch")
        frame["date"] = pd.to_datetime(frame["date"])
        return frame


class BaoStockFetcher:
    def __init__(self, cache: BacktestCache):
        self.cache = cache
        self.bs: Any = None

    def __enter__(self) -> "BaoStockFetcher":
        try:
            import baostock as bs
        except ImportError as exc:
            raise RuntimeError("请先运行：py -m pip install baostock") from exc
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"BaoStock 登录失败：{login.error_msg}")
        self.bs = bs
        return self

    def __exit__(self, *_args: Any) -> None:
        if self.bs is not None:
            self.bs.logout()

    def fetch(self, history_start: str, backtest_start: str, end: str) -> dict[str, Any]:
        calendar = _query_rows(self.bs.query_trade_dates(
            start_date=history_start,
            end_date=end,
        ))
        open_days = [row[0] for row in calendar if len(row) > 1 and row[1] == "1"]
        self.cache.db.executemany(
            "INSERT OR IGNORE INTO trading_days(date) VALUES (?)",
            ((item,) for item in open_days),
        )
        signal_days = _month_ends(day for day in open_days if day >= backtest_start)
        print(f"交易日 {len(open_days)} 个，月末信号日 {len(signal_days)} 个", flush=True)

        union: set[str] = set()
        for index, signal_day in enumerate(signal_days, 1):
            existing = self.cache.db.execute(
                "SELECT COUNT(*) FROM memberships WHERE signal_date=?",
                (signal_day,),
            ).fetchone()[0]
            if existing < 250:
                rows = _query_rows(self.bs.query_hs300_stocks(signal_day))
                self.cache.db.execute(
                    "DELETE FROM memberships WHERE signal_date=?",
                    (signal_day,),
                )
                self.cache.db.executemany(
                    "INSERT INTO memberships(signal_date,source_date,code,name) VALUES (?,?,?,?)",
                    ((signal_day, row[0], row[1], row[2]) for row in rows),
                )
            union.update(self.cache.members(signal_day))
            if index % 12 == 0 or index == len(signal_days):
                self.cache.db.commit()
                print(f"历史成分进度 {index}/{len(signal_days)}，累计 {len(union)} 只", flush=True)

        codes = sorted(union | {BENCHMARK_CODE})
        downloaded = 0
        skipped = 0
        errors: list[str] = []
        for index, code in enumerate(codes, 1):
            first, last, count = self.cache.price_coverage(code)
            if first and first <= history_start and last and last >= end and count >= 120:
                skipped += 1
                continue
            result = self.bs.query_history_k_data_plus(
                code,
                PRICE_FIELDS,
                start_date=history_start,
                end_date=end,
                frequency="d",
                adjustflag="2",
            )
            try:
                rows = _query_rows(result)
                if rows:
                    self.cache.upsert_price_rows(rows)
                    downloaded += 1
                else:
                    errors.append(f"{code}: 无日线")
            except Exception as exc:
                errors.append(f"{code}: {exc}")
            if index % 20 == 0 or index == len(codes):
                self.cache.db.commit()
                print(
                    f"日线进度 {index}/{len(codes)}，新增 {downloaded}，缓存命中 {skipped}，失败 {len(errors)}",
                    flush=True,
                )
        self.cache.db.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES ('last_fetch',?)",
            (datetime.now().isoformat(timespec="seconds"),),
        )
        self.cache.db.commit()
        return {
            "trading_days": len(open_days),
            "signal_days": len(signal_days),
            "unique_stocks": len(union),
            "downloaded": downloaded,
            "skipped": skipped,
            "errors": errors,
        }

    def fetch_industries(self, start: str, end: str) -> dict[str, Any]:
        signal_days = self.cache.membership_dates(start, end)
        if not signal_days:
            raise RuntimeError("缓存中没有历史成分，请先运行 fetch")
        fetched = 0
        for index, signal_day in enumerate(signal_days, 1):
            existing = self.cache.db.execute(
                "SELECT COUNT(*) FROM industries WHERE signal_date=?",
                (signal_day,),
            ).fetchone()[0]
            if existing >= 250:
                continue
            rows = _query_rows(self.bs.query_stock_industry(date=signal_day))
            member_set = set(self.cache.members(signal_day))
            payload = [
                (
                    signal_day,
                    row[0],
                    row[1],
                    row[3].strip() or f"未分类:{row[1]}",
                )
                for row in rows
                if len(row) >= 4 and row[1] in member_set
            ]
            self.cache.db.execute(
                "DELETE FROM industries WHERE signal_date=?",
                (signal_day,),
            )
            self.cache.db.executemany(
                "INSERT INTO industries(signal_date,source_date,code,industry) VALUES (?,?,?,?)",
                payload,
            )
            fetched += 1
            if index % 12 == 0 or index == len(signal_days):
                self.cache.db.commit()
                print(f"行业分类进度 {index}/{len(signal_days)}", flush=True)
        self.cache.db.commit()
        return {"signal_days": len(signal_days), "fetched": fetched}


class PointInTimeBacktester:
    def __init__(
        self,
        cache: BacktestCache,
        history_start: str,
        start: str,
        end: str,
        initial_capital: float = 100_000,
    ):
        self.cache = cache
        self.history_start = pd.Timestamp(history_start)
        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        self.initial_capital = initial_capital
        frame = cache.load_prices(history_start, end)
        self.by_code = {
            code: group.set_index("date").sort_index()
            for code, group in frame.groupby("code", sort=False)
        }
        self.trading_days = pd.DatetimeIndex(pd.to_datetime(
            cache.trading_days(start, end)
        ))
        raw_signals = cache.membership_dates(start, end)
        self.signal_days = [pd.Timestamp(item) for item in raw_signals]
        self.execution_for_signal: dict[pd.Timestamp, pd.Timestamp] = {}
        for signal in self.signal_days:
            later = self.trading_days[self.trading_days > signal]
            if len(later):
                self.execution_for_signal[signal] = later[0]
        self.features: dict[pd.Timestamp, pd.DataFrame] = {}
        self.regime: dict[pd.Timestamp, bool] = {}

    @staticmethod
    def _history_asof(frame: pd.DataFrame, signal: pd.Timestamp) -> pd.DataFrame:
        return frame.loc[frame.index <= signal].tail(253)

    def _feature_row(self, code: str, signal: pd.Timestamp) -> dict[str, Any] | None:
        frame = self.by_code.get(code)
        if frame is None:
            return None
        history = self._history_asof(frame, signal)
        if len(history) < 121 or history.index[-1] != signal:
            return None
        latest = history.iloc[-1]
        closes = history["close"].astype(float)
        returns = closes.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        if len(returns) < 60 or float(latest.get("tradestatus") or 0) != 1:
            return None
        if float(latest.get("is_st") or 0) != 0:
            return None
        amount20 = float(history["amount"].tail(20).mean())
        pct_chg = float(latest.get("pct_chg") or 0)
        pe = _float(latest.get("pe_ttm"))
        pb = _float(latest.get("pb_mrq"))
        close = float(closes.iloc[-1])
        if close <= 0 or amount20 < 200_000_000 or abs(pct_chg) > 6:
            return None
        if pe is not None and (pe <= 0 or pe > 80):
            return None
        if pb is not None and (pb <= 0 or pb > 10):
            return None

        ma20 = float(closes.tail(20).mean())
        ma60 = float(closes.tail(60).mean())
        return20 = close / float(closes.iloc[-21]) - 1
        return60 = close / float(closes.iloc[-61]) - 1
        return120 = close / float(closes.iloc[-121]) - 1
        momentum_live = return60 * 0.55 + return120 * 0.45
        momentum_live -= max(0.0, return20 - 0.18) * 1.8
        momentum_6_1 = float(closes.iloc[-21]) / float(closes.iloc[-121]) - 1
        if len(closes) >= 253:
            momentum_12_1 = float(closes.iloc[-21]) / float(closes.iloc[-253]) - 1
            momentum_6_1 = momentum_6_1 * 0.55 + momentum_12_1 * 0.45
        volatility = float(returns.tail(60).std(ddof=0))
        high120 = float(closes.tail(120).max())
        drawdown = close / high120 - 1
        downside = returns.tail(60).clip(upper=0)
        downside_vol = float(np.sqrt(np.mean(np.square(downside))))
        value_balanced = None
        value_cheap = None
        if pe is not None and pe > 0 and pb is not None and pb > 0:
            value_balanced = -abs(math.log(pe / 22)) * 0.65 - abs(math.log(pb / 2.5)) * 0.35
            value_cheap = -math.log(pe) * 0.65 - math.log(pb) * 0.35
        return {
            "code": code,
            "trend": (close / ma20 - 1) + (ma20 / ma60 - 1),
            "momentum_live": momentum_live,
            "momentum_6_1": momentum_6_1,
            "value_balanced": value_balanced,
            "value_cheap": value_cheap,
            "low_volatility": -volatility,
            "liquidity": math.log10(max(amount20, 1)),
            "risk_control": drawdown - downside_vol * 2,
            "return20": return20,
        }

    def prepare_features(self) -> None:
        benchmark = self.by_code.get(BENCHMARK_CODE)
        if benchmark is None:
            raise RuntimeError("缓存缺少沪深300指数行情")
        for index, signal in enumerate(self.signal_days, 1):
            members = self.cache.members(signal.date().isoformat())
            rows = [
                row for row in (
                    self._feature_row(code, signal) for code in members
                ) if row is not None
            ]
            self.features[signal] = pd.DataFrame(rows).set_index("code") if rows else pd.DataFrame()
            bench_history = benchmark.loc[benchmark.index <= signal]
            if len(bench_history) >= 120:
                close = float(bench_history["close"].iloc[-1])
                ma120 = float(bench_history["close"].tail(120).mean())
                self.regime[signal] = close >= ma120
            else:
                self.regime[signal] = True
            if index % 12 == 0 or index == len(self.signal_days):
                print(f"因子计算 {index}/{len(self.signal_days)}", flush=True)

    @staticmethod
    def score_frame(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
        if frame.empty:
            return pd.Series(dtype=float)
        score = pd.Series(0.0, index=frame.index)
        total = sum(weights.values())
        for factor, weight in weights.items():
            ranks = frame[factor].rank(pct=True, method="average").fillna(0.5)
            score += ranks * (weight / total) * 100
        score -= np.where(frame["return20"] > 0.22, 8.0, 0.0)
        return score.sort_values(ascending=False)

    def selections(self, variant: Variant) -> dict[pd.Timestamp, tuple[list[str], float]]:
        result: dict[pd.Timestamp, tuple[list[str], float]] = {}
        weights = STRATEGIES[variant.strategy]
        for signal, frame in self.features.items():
            scores = self.score_frame(frame, weights)
            exposure = 0.30 if variant.regime_filter and not self.regime.get(signal, True) else 1.0
            execution = self.execution_for_signal.get(signal)
            if execution is not None:
                if not variant.sector_cap:
                    selected = list(scores.head(variant.topk).index)
                else:
                    industries = self.cache.industries(signal.date().isoformat())
                    per_industry = max(1, math.ceil(variant.topk / 3))
                    counts: dict[str, int] = {}
                    selected = []
                    for code in scores.index:
                        industry = industries.get(code, f"未分类:{code}")
                        if counts.get(industry, 0) >= per_industry:
                            continue
                        selected.append(code)
                        counts[industry] = counts.get(industry, 0) + 1
                        if len(selected) >= variant.topk:
                            break
                result[execution] = (selected, exposure)
        return result

    @staticmethod
    def _buy_cost(notional: float) -> float:
        return max(notional * COMMISSION_RATE, MIN_COMMISSION) + notional * SLIPPAGE_RATE

    @staticmethod
    def _sell_cost(notional: float, day: pd.Timestamp) -> float:
        stamp = 0.0005 if day >= STAMP_TAX_CUTOVER else 0.001
        return (
            max(notional * COMMISSION_RATE, MIN_COMMISSION)
            + notional * (SLIPPAGE_RATE + stamp)
        )

    def run_variant(self, variant: Variant) -> pd.DataFrame:
        selections = self.selections(variant)
        equity = self.initial_capital
        cash = equity
        positions: dict[str, float] = {}
        previous_close: dict[str, float] = {}
        records: list[dict[str, Any]] = []

        for day in self.trading_days:
            start_equity = equity
            daily_turnover = 0.0
            daily_cost = 0.0
            if day in selections:
                # Mark existing holdings from yesterday's close to today's open.
                for code, value in list(positions.items()):
                    row = self.by_code.get(code)
                    if row is None or day not in row.index:
                        continue
                    open_price = _float(row.at[day, "open"])
                    prior = previous_close.get(code)
                    if open_price and prior:
                        positions[code] = value * open_price / prior
                sold = sum(positions.values())
                sell_cost = sum(self._sell_cost(value, day) for value in positions.values())
                cash += sold - sell_cost
                daily_turnover += sold
                daily_cost += sell_cost
                positions = {}
                previous_close = {}

                selected, exposure = selections[day]
                tradable: list[tuple[str, float, float]] = []
                for code in selected:
                    frame = self.by_code.get(code)
                    if frame is None or day not in frame.index:
                        continue
                    row = frame.loc[day]
                    open_price = _float(row.get("open"))
                    preclose = _float(row.get("preclose"))
                    close_price = _float(row.get("close"))
                    status = int(_float(row.get("tradestatus")) or 0)
                    if not open_price or not preclose or not close_price or status != 1:
                        continue
                    # A main-board limit-up open is treated as not fillable.
                    if open_price / preclose - 1 >= 0.095:
                        continue
                    tradable.append((code, open_price, close_price))
                investable = cash * exposure
                per_stock = investable / len(tradable) if tradable else 0
                for code, open_price, close_price in tradable:
                    cost = self._buy_cost(per_stock)
                    if per_stock + cost > cash:
                        per_stock = max(0.0, cash - cost)
                    if per_stock <= 0:
                        continue
                    cash -= per_stock + cost
                    positions[code] = per_stock * close_price / open_price
                    previous_close[code] = close_price
                    daily_turnover += per_stock
                    daily_cost += cost
            else:
                for code, value in list(positions.items()):
                    frame = self.by_code.get(code)
                    if frame is None or day not in frame.index:
                        continue
                    close_price = _float(frame.at[day, "close"])
                    prior = previous_close.get(code)
                    if close_price and prior:
                        positions[code] = value * close_price / prior
                        previous_close[code] = close_price

            equity = cash + sum(positions.values())
            records.append({
                "date": day,
                "equity": equity,
                "return": equity / start_equity - 1 if start_equity else 0,
                "turnover_notional": daily_turnover,
                "transaction_cost": daily_cost,
                "positions": len(positions),
            })
        result = pd.DataFrame(records).set_index("date")
        result.attrs["initial_capital"] = self.initial_capital
        return result

    def benchmark_returns(self) -> pd.Series:
        benchmark = self.by_code[BENCHMARK_CODE].reindex(self.trading_days)
        return benchmark["close"].pct_change().fillna(0.0)


def performance_metrics(
    returns: pd.Series,
    turnover_notional: pd.Series | None = None,
    transaction_cost: pd.Series | None = None,
    equity: pd.Series | None = None,
) -> dict[str, float]:
    clean = returns.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if clean.empty:
        return {key: 0.0 for key in (
            "total_return", "annual_return", "cagr", "volatility", "sharpe",
            "max_drawdown", "calmar", "monthly_win_rate", "gross_turnover",
            "annual_turnover", "transaction_cost_amount", "transaction_cost_rate",
            "annual_transaction_cost_rate", "trading_days", "trading_months",
            "period_years",
        )}
    curve = (1 + clean).cumprod()
    trading_months = int(clean.index.to_period("M").nunique())
    years = max(trading_months / 12, 1 / 12)
    total_return = float(curve.iloc[-1] - 1)
    annual_return = float(curve.iloc[-1] ** (1 / years) - 1)
    volatility = float(clean.std(ddof=0) * math.sqrt(252))
    sharpe = float(clean.mean() / clean.std(ddof=0) * math.sqrt(252)) if clean.std(ddof=0) else 0.0
    drawdown = curve / curve.cummax().clip(lower=1.0) - 1
    max_drawdown = float(drawdown.min())
    monthly = (1 + clean).resample("ME").prod() - 1
    aligned_equity = equity.reindex(clean.index).ffill() if equity is not None else None
    if aligned_equity is not None and not aligned_equity.empty:
        first_return = float(clean.iloc[0])
        denominator = 1 + first_return
        start_equity = (
            float(aligned_equity.iloc[0]) / denominator
            if denominator > 0 else float(aligned_equity.iloc[0])
        )
        average_equity = float(aligned_equity.mean())
    else:
        start_equity = 1.0
        average_equity = 1.0
    traded = float(turnover_notional.reindex(clean.index).fillna(0.0).sum()) if turnover_notional is not None else 0.0
    costs = float(transaction_cost.reindex(clean.index).fillna(0.0).sum()) if transaction_cost is not None else 0.0
    gross_turnover = traded / average_equity if average_equity > 0 else 0.0
    cost_rate = costs / start_equity if start_equity > 0 else 0.0
    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "cagr": annual_return,
        "volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "calmar": annual_return / abs(max_drawdown) if max_drawdown else 0.0,
        "monthly_win_rate": float((monthly > 0).mean()) if len(monthly) else 0.0,
        "gross_turnover": gross_turnover,
        "annual_turnover": gross_turnover / years,
        "transaction_cost_amount": costs,
        "transaction_cost_rate": cost_rate,
        "annual_transaction_cost_rate": cost_rate / years,
        "trading_days": int(len(clean)),
        "trading_months": trading_months,
        "period_years": years,
    }


def split_metrics(
    result: pd.DataFrame,
    benchmark: pd.Series,
    periods: dict[str, tuple[str, str]],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for name, (start, end) in periods.items():
        period_frame = result.loc[start:end]
        strategy_slice = period_frame["return"]
        benchmark_slice = benchmark.loc[start:end]
        strategy_metrics = performance_metrics(
            strategy_slice,
            turnover_notional=period_frame["turnover_notional"],
            transaction_cost=period_frame["transaction_cost"],
            equity=period_frame["equity"],
        )
        benchmark_metrics = performance_metrics(benchmark_slice)
        annual_excess = strategy_metrics["annual_return"] - benchmark_metrics["annual_return"]
        strategy_metrics["annual_excess_return"] = annual_excess
        strategy_metrics["excess_cagr"] = annual_excess
        actual_start = strategy_slice.index.min()
        actual_end = strategy_slice.index.max()
        output[name] = {
            "period": {
                "configured_start": start,
                "configured_end": end,
                "actual_start": actual_start.date().isoformat() if pd.notna(actual_start) else "",
                "actual_end": actual_end.date().isoformat() if pd.notna(actual_end) else "",
                "trading_days": strategy_metrics["trading_days"],
                "trading_months": strategy_metrics["trading_months"],
            },
            "strategy": strategy_metrics,
            "benchmark": benchmark_metrics,
        }
    return output


def robust_selection_score(metrics: dict[str, dict[str, dict[str, float]]]) -> float:
    folds = [metrics[name]["strategy"] for name in (
        "cv_2019_2020", "cv_2021_2022", "cv_2023_2024",
    )]
    cagrs = [item["cagr"] for item in folds]
    excess = [item["excess_cagr"] for item in folds]
    sharpes = [item["sharpe"] for item in folds]
    positive_ratio = sum(value > 0 for value in cagrs) / len(cagrs)
    drawdown_penalty = max(0.0, max(abs(item["max_drawdown"]) for item in folds) - 0.25)
    loss_penalty = max(0.0, -min(cagrs))
    return (
        statistics.median(cagrs) * 2
        + statistics.median(excess) * 2
        + min(sharpes) * 0.35
        + positive_ratio * 0.20
        - drawdown_penalty * 2
        - loss_penalty * 3
    )


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _money(value: float) -> str:
    return f"{value:,.0f} 元"


def _variant_from_item(item: dict[str, Any]) -> Variant:
    return Variant(
        strategy=item["strategy"],
        topk=int(item["topk"]),
        regime_filter=bool(item["regime_filter"]),
        sector_cap=bool(item["sector_cap"]),
    )


def write_comparison_artifacts(
    report_path: str | Path,
    winner: dict[str, Any],
    deployed: dict[str, Any],
    winner_result: pd.DataFrame,
    deployed_result: pd.DataFrame,
    benchmark: pd.Series,
) -> tuple[dict[str, str], pd.DataFrame]:
    target_dir = Path(report_path).parent
    target_dir.mkdir(parents=True, exist_ok=True)
    net_value_path = target_dir / "backtest-net-value.png"
    drawdown_path = target_dir / "backtest-drawdown.png"
    monthly_path = target_dir / "backtest-monthly-returns.csv"
    daily_path = target_dir / "backtest-daily-curves.csv"

    daily_returns = pd.concat({
        "historical_best": winner_result["return"],
        "online_conservative": deployed_result["return"],
        "csi300": benchmark,
    }, axis=1).fillna(0.0)
    net_value = (1 + daily_returns).cumprod()
    drawdown = net_value / net_value.cummax().clip(lower=1.0) - 1
    monthly = (1 + daily_returns).resample("ME").prod() - 1
    monthly.index = monthly.index.to_period("M").astype(str)
    monthly.index.name = "month"
    monthly.to_csv(monthly_path, encoding="utf-8-sig", float_format="%.10f")

    daily_export = pd.concat({
        "return": daily_returns,
        "net_value": net_value,
        "drawdown": drawdown,
    }, axis=1)
    daily_export.columns = [f"{kind}_{series}" for kind, series in daily_export.columns]
    daily_export.index.name = "date"
    daily_export.to_csv(daily_path, encoding="utf-8-sig", float_format="%.10f")

    import os
    import tempfile

    mpl_config = Path(tempfile.gettempdir()) / "weixin-stock-backtest-matplotlib"
    mpl_config.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_config))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {
        "historical_best": "#0072B2",
        "online_conservative": "#D55E00",
        "csi300": "#009E73",
    }
    labels = {
        "historical_best": f"Historical best ({winner['key']})",
        "online_conservative": f"Online conservative ({deployed['key']})",
        "csi300": "CSI 300",
    }

    def plot_frame(frame: pd.DataFrame, output: Path, title: str, ylabel: str) -> None:
        figure, axis = plt.subplots(figsize=(13, 6.5), dpi=160)
        for column in frame.columns:
            axis.plot(
                frame.index,
                frame[column],
                label=labels[column],
                color=colors[column],
                linewidth=1.7,
            )
        for boundary, name in (("2023-01-01", "Validation"), ("2025-01-01", "Holdout")):
            stamp = pd.Timestamp(boundary)
            axis.axvline(stamp, color="#666666", linestyle="--", linewidth=1.0, alpha=0.7)
            axis.text(stamp, axis.get_ylim()[1], name, va="top", ha="left", fontsize=8)
        axis.set_title(title)
        axis.set_xlabel("Date")
        axis.set_ylabel(ylabel)
        axis.grid(True, color="#D9D9D9", linewidth=0.6, alpha=0.8)
        axis.legend(loc="best", fontsize=8)
        figure.tight_layout()
        figure.savefig(output, bbox_inches="tight")
        plt.close(figure)

    baseline_date = net_value.index.min() - pd.Timedelta(days=1)
    plot_net_value = pd.concat([
        pd.DataFrame(1.0, index=[baseline_date], columns=net_value.columns),
        net_value,
    ])
    plot_drawdown = pd.concat([
        pd.DataFrame(0.0, index=[baseline_date], columns=drawdown.columns),
        drawdown,
    ])
    plot_frame(plot_net_value, net_value_path, "Backtest net value (net of costs)", "Net value")
    plot_frame(plot_drawdown, drawdown_path, "Backtest drawdown", "Drawdown")
    return ({
        "net_value": str(net_value_path),
        "drawdown": str(drawdown_path),
        "monthly_returns": str(monthly_path),
        "daily_curves": str(daily_path),
    }, monthly)


def write_report(
    path: str | Path,
    ranked: list[dict[str, Any]],
    periods: dict[str, tuple[str, str]],
    cache_stats: dict[str, Any],
    artifacts: dict[str, str],
    monthly_returns: pd.DataFrame,
) -> None:
    winner = ranked[0]
    baseline = next(
        item for item in ranked
        if item["key"] == "live_proxy:top3:always:uncapped"
    )
    deployed = next(
        item for item in ranked
        if item["key"] == "deployed_blend:top3:always:sector-cap"
    )
    period_labels = {
        "train": "训练期",
        "validation": "验证期",
        "holdout": "留出期",
        "cv_2019_2020": "2019-2020",
        "cv_2021_2022": "2021-2022",
        "cv_2023_2024": "2023-2024",
    }
    lines = [
        "# 微信 Bot v0.9.0 选股策略统一口径回测",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 统计口径",
        "",
        "- 股票池：每个月末当时的沪深300成分股，不使用今天成分股回填历史。",
        "- 信号：月末收盘后计算；交易：下一个交易日开盘，避免同日未来数据。",
        "- 成本：佣金双边 0.03%（每笔最低 5 元）、滑点双边 0.05%、卖出印花税按 2023-08-28 前后分别采用 0.10%/0.05%。",
        "- 限制：停牌、ST、流动性不足和主板涨停开盘不买；初始模拟资金 10 万元。",
        "- 分散：sector-cap 方案按当时行业分类限制集中度，前三只最多一只属于同一细分行业。",
        "- 策略累计收益：每日扣除佣金、滑点和印花税后的净收益复利。",
        "- 年化收益：按区间实际交易月数折算，即 `(1 + 累计收益)^(12 / 交易月数) - 1`。",
        "- 年化超额收益：策略净年化收益减去沪深300年化收益，不是累计收益差。",
        "- 夏普比率：日收益、年化252个交易日、无风险利率按0；Calmar为年化收益除以最大回撤绝对值。",
        "- 年化换手率：区间买入与卖出成交额之和除以区间平均净资产，再除以区间年数；这是双边总换手。",
        "- 策略按月整体换仓，双边年化换手理论上接近24倍，因此表中约2300%的数值不是小数点错误。",
        "- 交易成本：区间实际扣除金额，并同时列出其占区间期初权益的比例；成本已进入策略净值，不重复扣除。",
        f"- 数据量：{cache_stats.get('unique_stocks', '缓存')} 只历史成分，数据来源 BaoStock。",
        "",
        "## Qlib 字段说明",
        "",
        "本回测没有调用 Qlib 的回测引擎或 `PortAnaRecord`，因此不存在“Qlib 输出选择 `return` 还是 `excess_return_with_cost`”这一字段选择。程序输出的是自研回测器的 `strategy_net_return_after_cost`，佣金、滑点和印花税已经逐笔扣除；再用该净年化收益减去沪深300年化收益得到 `annual_excess_return_after_cost`。若只做语义映射，它更接近 Qlib 的 `excess_return_with_cost`，但不能标记成 Qlib 原生输出。",
        "",
        "## 数据分段",
        "",
        "| 区间 | 配置开始 | 配置结束 | 首个交易日 | 最后交易日 | 交易月数 | 交易日数 |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for name in ("train", "validation", "holdout"):
        meta = winner["metrics"][name]["period"]
        lines.append(
            f"| {period_labels[name]} | {meta['configured_start']} | {meta['configured_end']} | "
            f"{meta['actual_start']} | {meta['actual_end']} | {meta['trading_months']} | {meta['trading_days']} |"
        )
    lines.extend([
        "",
        "参数只按训练期与验证期排序；留出期不参与挑选。",
        "",
        "## 训练与验证排名",
        "",
        "| 排名 | 方案 | 训练年化收益 | 训练年化超额 | 验证年化收益 | 验证年化超额 | 验证最大回撤 | 稳健分 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ])
    for index, item in enumerate(ranked[:12], 1):
        train = item["metrics"]["train"]["strategy"]
        valid = item["metrics"]["validation"]["strategy"]
        lines.append(
            f"| {index} | {item['key']} | {_percent(train['cagr'])} | "
            f"{_percent(train['excess_cagr'])} | {_percent(valid['cagr'])} | "
            f"{_percent(valid['excess_cagr'])} | {_percent(valid['max_drawdown'])} | "
            f"{item['selection_score']:.3f} |"
        )
    baseline_test = baseline["metrics"]["holdout"]["strategy"]
    winner_test = winner["metrics"]["holdout"]["strategy"]
    deployed_test = deployed["metrics"]["holdout"]["strategy"]
    lines.extend([
        "",
        "## 收益统一对比",
        "",
        f"- 历史最优策略：`{winner['key']}`",
        f"- 线上保守策略代理：`{deployed['key']}`",
        "- 以下所有策略收益均已扣除交易成本；沪深300为指数价格收益，不扣交易成本。",
        "",
        "| 区间 | 策略 | 策略累计收益 | 策略年化收益 | 沪深300累计收益 | 沪深300年化收益 | 年化超额收益 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ])
    for name in ("train", "validation", "holdout"):
        for label, item in (("历史最优", winner), ("线上保守", deployed)):
            strategy = item["metrics"][name]["strategy"]
            benchmark_metrics = item["metrics"][name]["benchmark"]
            lines.append(
                f"| {period_labels[name]} | {label} | {_percent(strategy['total_return'])} | "
                f"{_percent(strategy['annual_return'])} | {_percent(benchmark_metrics['total_return'])} | "
                f"{_percent(benchmark_metrics['annual_return'])} | "
                f"{_percent(strategy['annual_excess_return'])} |"
            )
    lines.extend([
        "",
        "## 风险、换手与成本对比",
        "",
        "| 区间 | 策略 | 最大回撤 | 夏普 | Calmar | 月度胜率 | 双边年化换手 | 累计交易成本 | 成本/期初权益 | 年化成本率 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for name in ("train", "validation", "holdout"):
        for label, item in (("历史最优", winner), ("线上保守", deployed)):
            strategy = item["metrics"][name]["strategy"]
            lines.append(
                f"| {period_labels[name]} | {label} | {_percent(strategy['max_drawdown'])} | "
                f"{strategy['sharpe']:.2f} | {strategy['calmar']:.2f} | "
                f"{_percent(strategy['monthly_win_rate'])} | {_percent(strategy['annual_turnover'])} | "
                f"{_money(strategy['transaction_cost_amount'])} | {_percent(strategy['transaction_cost_rate'])} | "
                f"{_percent(strategy['annual_transaction_cost_rate'])} |"
            )
    lines.extend([
        "",
        "## 滚动窗口收益口径",
        "",
        "此前提到的2019-2020、2021-2022、2023-2024百分比均指年化收益，不是累计收益。为消除歧义，下面同时列出累计与年化：",
        "",
        "| 窗口 | 交易月数 | 历史最优累计 | 历史最优年化 | 线上保守累计 | 线上保守年化 | 沪深300累计 | 沪深300年化 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for name in ("cv_2019_2020", "cv_2021_2022", "cv_2023_2024", "holdout"):
        winner_metrics = winner["metrics"][name]["strategy"]
        deployed_metrics = deployed["metrics"][name]["strategy"]
        benchmark_metrics = winner["metrics"][name]["benchmark"]
        lines.append(
            f"| {period_labels[name]} | {winner_metrics['trading_months']} | "
            f"{_percent(winner_metrics['total_return'])} | {_percent(winner_metrics['annual_return'])} | "
            f"{_percent(deployed_metrics['total_return'])} | {_percent(deployed_metrics['annual_return'])} | "
            f"{_percent(benchmark_metrics['total_return'])} | {_percent(benchmark_metrics['annual_return'])} |"
        )
    lines.extend([
        "",
        "## 净值与回撤曲线",
        "",
        "三条曲线均从2019年首个交易日开始，策略曲线为扣除成本后的净值；虚线分别标出验证期和留出期起点。整段回撤图相对2019年以来的历史高点计算，而上面的分区间指标会在各区间起点重新归一化为1，因此两者回答的问题不同。",
        "",
        f"![策略与沪深300净值曲线]({Path(artifacts['net_value']).name})",
        "",
        f"![策略与沪深300回撤曲线]({Path(artifacts['drawdown']).name})",
        "",
        f"逐日净值、收益和回撤原始表：[{Path(artifacts['daily_curves']).name}]({Path(artifacts['daily_curves']).name})",
        "",
        "## 逐月收益表",
        "",
        f"完整机器可读文件：[{Path(artifacts['monthly_returns']).name}]({Path(artifacts['monthly_returns']).name})",
        "",
        "| 月份 | 历史最优 | 线上保守 | 沪深300 |",
        "|---|---:|---:|---:|",
    ])
    for month, row in monthly_returns.iterrows():
        lines.append(
            f"| {month} | {_percent(float(row['historical_best']))} | "
            f"{_percent(float(row['online_conservative']))} | {_percent(float(row['csi300']))} |"
        )
    lines.extend([
        "",
        "## 为什么仍上线线上保守策略",
        "",
        f"留出期事实是：历史最优年化 {_percent(winner_test['annual_return'])}、最大回撤 {_percent(winner_test['max_drawdown'])}；线上保守年化 {_percent(deployed_test['annual_return'])}、最大回撤 {_percent(deployed_test['max_drawdown'])}。线上保守在这两项上都更差，不能用“回测更安全”解释上线。",
        "",
        "1. “保守”是相对于旧线上策略而言，不是相对于事后选出的历史最优。旧线上代理留出期最大回撤为 "
        f"{_percent(baseline_test['max_drawdown'])}，v0.9.0线上保守代理为 {_percent(deployed_test['max_drawdown'])}。",
        "2. 历史最优从72个组合中经训练期与验证期排序选出，仍存在模型选择偏差；它持有5只且更偏低波动/估值，天然比线上3只持仓更分散，所以留出期回撤更小并不矛盾。",
        "3. 线上真实策略还使用10%点时基本面质量和72分准入门槛；由于本历史数据没有可靠的点时质量快照，回测中的 `deployed_blend` 只是价格与估值代理，并非线上逻辑的逐笔完全复刻。",
        "4. 上线选择保留多因子结构、行业分散和原有持仓工作流，避免仅凭一次参数搜索就把生产策略整体换成低波动/价值组合。这个理由属于抗过拟合与运行连续性，不代表它的历史绩效更好。",
        "5. 严格按当前回测证据，无法证明线上保守策略优于历史最优；因此v0.9.0应视为临时生产基线，历史最优应作为并行模拟挑战者做前向检验，而不是宣称线上方案已经胜出。",
        "",
        "## 结论约束",
        "",
        "回测用于比较规则，不保证未来盈利。即便采用历史成分与点时估值，公开数据仍可能有复权、涨跌停成交和退市数据误差。继续在同一留出期上调参会污染留出期，因此下一步应冻结参数并做前向模拟盘A/B记录。",
        "",
    ])
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")


def run_research(args: argparse.Namespace, cache: BacktestCache) -> dict[str, Any]:
    backtester = PointInTimeBacktester(
        cache,
        history_start=args.history_start,
        start=args.start,
        end=args.end,
        initial_capital=args.capital,
    )
    backtester.prepare_features()
    benchmark = backtester.benchmark_returns()
    periods = {
        "train": (args.train_start, args.train_end),
        "validation": (args.validation_start, args.validation_end),
        "cv_2019_2020": ("2019-01-01", "2020-12-31"),
        "cv_2021_2022": ("2021-01-01", "2022-12-31"),
        "cv_2023_2024": ("2023-01-01", "2024-12-31"),
        "holdout": (args.holdout_start, args.end),
    }
    variants = [
        Variant(strategy, topk, regime, sector_cap)
        for strategy in STRATEGIES
        for topk in (3, 5, 10)
        for regime in (False, True)
        for sector_cap in (False, True)
    ]
    ranked: list[dict[str, Any]] = []
    for index, variant in enumerate(variants, 1):
        result = backtester.run_variant(variant)
        metrics = split_metrics(result, benchmark, periods)
        ranked.append({
            "key": variant.key,
            "strategy": variant.strategy,
            "topk": variant.topk,
            "regime_filter": variant.regime_filter,
            "sector_cap": variant.sector_cap,
            "weights": STRATEGIES[variant.strategy],
            "metrics": metrics,
            "selection_score": robust_selection_score(metrics),
        })
        if index % 10 == 0 or index == len(variants):
            print(f"策略回测 {index}/{len(variants)}", flush=True)
    ranked.sort(key=lambda item: item["selection_score"], reverse=True)
    stats = {
        "unique_stocks": cache.db.execute(
            "SELECT COUNT(DISTINCT code) FROM memberships"
        ).fetchone()[0],
        "price_rows": cache.db.execute("SELECT COUNT(*) FROM prices").fetchone()[0],
    }
    winner = ranked[0]
    deployed = next(
        item for item in ranked
        if item["key"] == "deployed_blend:top3:always:sector-cap"
    )
    winner_result = backtester.run_variant(_variant_from_item(winner))
    deployed_result = backtester.run_variant(_variant_from_item(deployed))
    artifacts, monthly_returns = write_comparison_artifacts(
        args.report,
        winner,
        deployed,
        winner_result,
        deployed_result,
        benchmark,
    )
    write_report(
        args.report,
        ranked,
        periods,
        stats,
        artifacts,
        monthly_returns,
    )
    result_path = Path(args.result_json)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "engine": "custom_point_in_time_backtester",
        "return_field": "strategy_net_return_after_cost",
        "qlib_output": "not_applicable",
        "annual_excess_field": "strategy_annual_return_after_cost_minus_csi300_annual_return",
        "periods": periods,
        "cache": stats,
        "artifacts": artifacts,
        "comparison": {
            "historical_best": winner["key"],
            "online_conservative": deployed["key"],
            "monthly_returns": monthly_returns.reset_index().to_dict(orient="records"),
        },
        "variants": ranked,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return winner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("fetch", "fetch-industries", "run", "all"),
        nargs="?",
        default="all",
    )
    parser.add_argument("--db", default="data/backtest-csi300.sqlite")
    parser.add_argument("--history-start", default="2017-01-01")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--train-start", default="2019-01-01")
    parser.add_argument("--train-end", default="2022-12-31")
    parser.add_argument("--validation-start", default="2023-01-01")
    parser.add_argument("--validation-end", default="2024-12-31")
    parser.add_argument("--holdout-start", default="2025-01-01")
    parser.add_argument("--capital", type=float, default=100_000)
    parser.add_argument("--report", default="records/stocks/选股策略点时回测.md")
    parser.add_argument("--result-json", default="records/stocks/backtest-results.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cache = BacktestCache(args.db)
    fetch_stats: dict[str, Any] = {}
    try:
        if args.action in {"fetch", "all"}:
            with BaoStockFetcher(cache) as fetcher:
                fetch_stats = fetcher.fetch(
                    args.history_start,
                    args.start,
                    args.end,
                )
                industry_stats = fetcher.fetch_industries(args.start, args.end)
                fetch_stats["industries"] = industry_stats
            print(json.dumps(fetch_stats, ensure_ascii=False), flush=True)
        elif args.action == "fetch-industries":
            with BaoStockFetcher(cache) as fetcher:
                industry_stats = fetcher.fetch_industries(args.start, args.end)
            print(json.dumps(industry_stats, ensure_ascii=False), flush=True)
        if args.action in {"run", "all"}:
            winner = run_research(args, cache)
            print(json.dumps({
                "winner": winner["key"],
                "selection_score": winner["selection_score"],
                "holdout": winner["metrics"]["holdout"],
                "report": args.report,
            }, ensure_ascii=False, indent=2), flush=True)
    finally:
        cache.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"回测失败：{exc}", file=sys.stderr)
        raise
