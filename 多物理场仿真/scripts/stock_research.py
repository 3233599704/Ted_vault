"""A-share research helpers for the Feishu bot.

The module keeps market data, deterministic scoring, persistence, and report
formatting separate from Feishu message handling. AKShare is imported lazily so
the existing bot can still start and explain a missing optional dependency.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol


DISCLAIMER = "仅供同花顺模拟盘学习和观察，不构成真实交易建议。"
STOCK_CONTEXT_WORDS = (
    "股票", "个股", "股价", "走势", "趋势", "模拟盘", "值得", "入手",
    "关注", "分析", "看看", "看下", "看一看", "行情",
)
CODE_PATTERN = re.compile(
    r"(?<!\d)(?:(?:sh|sz|bj)[.\s-]?)?([0-9]{6})(?:[.\s-]?(?:sh|sz|bj))?(?!\d)",
    re.IGNORECASE,
)
VALID_A_SHARE_PREFIXES = (
    "000", "001", "002", "003", "300", "301",
    "600", "601", "603", "605", "688",
    "430", "431", "432", "433", "434", "435", "436", "437", "438", "439",
    "8", "920",
)
RISK_NAME_MARKERS = ("ST", "*ST", "退", "N")
RISK_NOTICE_WORDS = ("减持", "处罚", "立案", "诉讼", "风险提示", "亏损", "退市")
POSITIVE_NOTICE_WORDS = ("回购", "增持", "中标", "预增", "扭亏")


def _number(value: Any) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        if isinstance(value, str):
            cleaned = value.strip().replace(",", "").replace("%", "")
            multiplier = 1.0
            if cleaned.endswith("亿"):
                cleaned = cleaned[:-1]
                multiplier = 100_000_000
            elif cleaned.endswith("万"):
                cleaned = cleaned[:-1]
                multiplier = 10_000
            result = float(cleaned) * multiplier
        else:
            result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, list):
        return [dict(item) for item in frame]
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict("records"))
    return []


def normalize_stock_code(value: str) -> str | None:
    match = CODE_PATTERN.search(value.strip())
    if not match:
        return None
    code = match.group(1)
    if not code.startswith(VALID_A_SHARE_PREFIXES):
        return None
    return code


def extract_stock_codes(text: str, require_context: bool = True) -> list[str]:
    """Extract unique A-share-shaped codes from text.

    Natural-language detection requires a stock-related word to avoid treating
    dates, verification codes, and other six-digit numbers as stocks.
    """
    lowered = text.lower()
    if require_context and not any(word in lowered for word in STOCK_CONTEXT_WORDS):
        return []
    found: list[str] = []
    for match in CODE_PATTERN.finditer(text):
        code = normalize_stock_code(match.group(0))
        if code and code not in found:
            found.append(code)
    return found


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)


class WatchlistStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _load_unlocked(self) -> dict[str, list[str]]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(user): [
                code for code in values
                if isinstance(code, str) and normalize_stock_code(code)
            ]
            for user, values in raw.items()
            if isinstance(values, list)
        }

    def list(self, user_id: str) -> list[str]:
        with self.lock:
            return list(self._load_unlocked().get(user_id, []))

    def add(self, user_id: str, code: str) -> bool:
        normalized = normalize_stock_code(code)
        if not normalized:
            raise ValueError("不是支持的 A 股代码")
        with self.lock:
            data = self._load_unlocked()
            values = data.setdefault(user_id, [])
            if normalized in values:
                return False
            values.append(normalized)
            _atomic_write_json(self.path, data)
            return True

    def remove(self, user_id: str, code: str) -> bool:
        normalized = normalize_stock_code(code)
        if not normalized:
            raise ValueError("不是支持的 A 股代码")
        with self.lock:
            data = self._load_unlocked()
            values = data.get(user_id, [])
            if normalized not in values:
                return False
            values.remove(normalized)
            if values:
                data[user_id] = values
            else:
                data.pop(user_id, None)
            _atomic_write_json(self.path, data)
            return True


class StockDataProvider(Protocol):
    name: str

    def market_snapshot(self) -> list[dict[str, Any]]: ...

    def history(self, code: str, start: date, end: date) -> list[dict[str, Any]]: ...

    def latest_financials(self, today: date) -> dict[str, dict[str, Any]]: ...

    def recent_notices(self, today: date, days: int = 7) -> dict[str, list[str]]: ...

    def trading_days(self) -> set[date]: ...


class AkshareProvider:
    """AKShare-backed provider with small retries and defensive field mapping."""

    name = "AKShare（公开市场数据，可能有延迟）"

    def __init__(self, retries: int = 2, retry_delay: float = 1.0):
        self.retries = retries
        self.retry_delay = retry_delay
        self._module: Any = None

    def _ak(self) -> Any:
        if self._module is None:
            try:
                import akshare as ak
            except ImportError as exc:
                raise RuntimeError(
                    "股票功能需要 AKShare。请运行：py -m pip install akshare"
                ) from exc
            self._module = ak
        return self._module

    def _call(self, name: str, **kwargs: Any) -> Any:
        function = getattr(self._ak(), name)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return function(**kwargs)
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.retry_delay * (attempt + 1))
        raise RuntimeError(f"AKShare {name} 获取失败：{last_error}") from last_error

    def market_snapshot(self) -> list[dict[str, Any]]:
        return _records(self._call("stock_zh_a_spot_em"))

    def history(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        frame = self._call(
            "stock_zh_a_hist",
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        return _records(frame)

    @staticmethod
    def _quarter_dates(today: date) -> list[str]:
        candidates: list[date] = []
        for year in (today.year, today.year - 1):
            for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
                candidate = date(year, month, day)
                if candidate <= today:
                    candidates.append(candidate)
        return [item.strftime("%Y%m%d") for item in sorted(candidates, reverse=True)]

    def latest_financials(self, today: date) -> dict[str, dict[str, Any]]:
        last_error: Exception | None = None
        for report_date in self._quarter_dates(today):
            try:
                rows = _records(self._call("stock_yjbb_em", date=report_date))
            except Exception as exc:
                last_error = exc
                continue
            if not rows:
                continue
            result: dict[str, dict[str, Any]] = {}
            for row in rows:
                code = str(_first(row, "股票代码", "代码") or "").zfill(6)
                if normalize_stock_code(code):
                    item = dict(row)
                    item["_报告期"] = report_date
                    result[code] = item
            if result:
                return result
        if last_error:
            raise RuntimeError(f"最新业绩数据获取失败：{last_error}")
        return {}

    def recent_notices(self, today: date, days: int = 7) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for offset in range(days):
            target = today - timedelta(days=offset)
            if target.weekday() >= 5:
                continue
            try:
                rows = _records(
                    self._call(
                        "stock_notice_report",
                        symbol="全部",
                        date=target.strftime("%Y%m%d"),
                    )
                )
            except Exception:
                continue
            for row in rows:
                code = str(_first(row, "代码", "股票代码") or "").zfill(6)
                title = str(_first(row, "公告标题", "标题") or "").strip()
                if normalize_stock_code(code) and title:
                    result.setdefault(code, []).append(title)
        return result

    def trading_days(self) -> set[date]:
        rows = _records(self._call("tool_trade_date_hist_sina"))
        result: set[date] = set()
        for row in rows:
            value = _first(row, "trade_date", "交易日")
            if value is None:
                continue
            if isinstance(value, datetime):
                result.add(value.date())
            elif isinstance(value, date):
                result.add(value)
            else:
                try:
                    result.add(datetime.fromisoformat(str(value)).date())
                except ValueError:
                    continue
        return result


@dataclass
class StockAnalysis:
    code: str
    name: str
    score: int
    trend: str
    price: float
    data_date: str
    reasons: list[str]
    risks: list[str]
    facts: list[str]


class StockResearchService:
    """Screen the A-share market and format beginner-friendly reports."""

    def __init__(
        self,
        provider: StockDataProvider | None = None,
        cache_seconds: int = 4 * 60 * 60,
        shortlist_size: int = 35,
    ):
        self.provider = provider or AkshareProvider()
        self.cache_seconds = cache_seconds
        self.shortlist_size = shortlist_size
        self._lock = threading.Lock()
        self._market_cache: tuple[float, list[StockAnalysis]] | None = None
        self._snapshot_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
        self._financial_cache: tuple[date, dict[str, dict[str, Any]]] | None = None
        self._notice_cache: tuple[date, dict[str, list[str]]] | None = None
        self._calendar_cache: tuple[float, set[date]] | None = None

    @staticmethod
    def _snapshot_code(row: dict[str, Any]) -> str:
        return str(_first(row, "代码", "股票代码") or "").zfill(6)

    @staticmethod
    def _snapshot_name(row: dict[str, Any]) -> str:
        return str(_first(row, "名称", "股票简称") or "").strip()

    def _snapshot(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        if self._snapshot_cache and now - self._snapshot_cache[0] < self.cache_seconds:
            return self._snapshot_cache[1]
        rows = self.provider.market_snapshot()
        result = {
            self._snapshot_code(row): row
            for row in rows
            if normalize_stock_code(self._snapshot_code(row))
        }
        if not result:
            raise RuntimeError("全市场行情为空，已停止本次筛选")
        self._snapshot_cache = (now, result)
        return result

    def _financials(self, today: date) -> dict[str, dict[str, Any]]:
        if self._financial_cache and self._financial_cache[0] == today:
            return self._financial_cache[1]
        try:
            data = self.provider.latest_financials(today)
        except Exception:
            data = {}
        self._financial_cache = (today, data)
        return data

    def _notices(self, today: date) -> dict[str, list[str]]:
        if self._notice_cache and self._notice_cache[0] == today:
            return self._notice_cache[1]
        try:
            data = self.provider.recent_notices(today)
        except Exception:
            data = {}
        self._notice_cache = (today, data)
        return data

    @staticmethod
    def _eligible_snapshot(row: dict[str, Any]) -> bool:
        name = StockResearchService._snapshot_name(row).upper()
        price = _number(_first(row, "最新价", "收盘"))
        change = _number(_first(row, "涨跌幅"))
        amount = _number(_first(row, "成交额"))
        turnover = _number(_first(row, "换手率"))
        pe = _number(_first(row, "市盈率-动态", "动态市盈率", "市盈率"))
        pb = _number(_first(row, "市净率"))
        if not name or any(marker in name for marker in RISK_NAME_MARKERS):
            return False
        if price is None or price < 2 or price > 500:
            return False
        if change is None or change < -4 or change > 5.5:
            return False
        if amount is None or amount < 100_000_000:
            return False
        if turnover is not None and (turnover < 0.3 or turnover > 15):
            return False
        if pe is not None and (pe <= 0 or pe > 100):
            return False
        if pb is not None and (pb <= 0 or pb > 12):
            return False
        return True

    @staticmethod
    def _preliminary_score(row: dict[str, Any]) -> float:
        change = _number(_first(row, "涨跌幅")) or 0
        amount = _number(_first(row, "成交额")) or 0
        volume_ratio = _number(_first(row, "量比")) or 1
        turnover = _number(_first(row, "换手率")) or 0
        pe = _number(_first(row, "市盈率-动态", "动态市盈率", "市盈率"))
        pb = _number(_first(row, "市净率"))
        score = min(math.log10(max(amount, 1)) * 4, 40)
        score += max(0, 8 - abs(change - 1.5) * 2)
        score += max(0, 6 - abs(volume_ratio - 1.5) * 3)
        score += max(0, 5 - abs(turnover - 3) * 0.8)
        if pe is not None and 5 <= pe <= 45:
            score += 6
        if pb is not None and 0.5 <= pb <= 5:
            score += 4
        return score

    @staticmethod
    def _history_values(
        rows: list[dict[str, Any]],
        *fields: str,
    ) -> list[float]:
        result: list[float] = []
        for row in rows:
            value = _number(_first(row, *fields))
            if value is not None:
                result.append(value)
        return result

    @staticmethod
    def _percent_change(new: float, old: float) -> float:
        return (new / old - 1) * 100 if old else 0

    def _analyze(
        self,
        code: str,
        snapshot: dict[str, Any],
        history: list[dict[str, Any]],
        financial: dict[str, Any] | None,
        notices: list[str],
    ) -> StockAnalysis:
        history = sorted(history, key=lambda row: str(_first(row, "日期", "date") or ""))
        closes = self._history_values(history, "收盘", "close")
        volumes = self._history_values(history, "成交量", "volume")
        if len(closes) < 60:
            raise ValueError("可用历史行情不足 60 个交易日")

        latest = closes[-1]
        ma5 = statistics.fmean(closes[-5:])
        ma20 = statistics.fmean(closes[-20:])
        ma60 = statistics.fmean(closes[-60:])
        return5 = self._percent_change(latest, closes[-6])
        return20 = self._percent_change(latest, closes[-21])
        return60 = self._percent_change(latest, closes[-60])
        high60 = max(closes[-60:])
        drawdown = self._percent_change(latest, high60)
        daily_returns = [
            self._percent_change(closes[index], closes[index - 1])
            for index in range(len(closes) - 19, len(closes))
        ]
        volatility = statistics.pstdev(daily_returns)
        volume_ratio = None
        if len(volumes) >= 6 and statistics.fmean(volumes[-6:-1]) > 0:
            volume_ratio = volumes[-1] / statistics.fmean(volumes[-6:-1])

        score = 45
        reasons: list[str] = []
        risks: list[str] = []
        facts: list[str] = []

        if latest > ma20 > ma60:
            score += 18
            reasons.append("最近一到三个月整体保持向上")
            trend = "稳步向上"
        elif latest > ma20:
            score += 8
            reasons.append("最近一个月有所转强")
            trend = "正在转强"
        elif latest < ma20 < ma60:
            score -= 18
            risks.append("最近一到三个月整体仍在走弱")
            trend = "偏弱"
        else:
            trend = "来回震荡"
            risks.append("方向还不够明确，价格上下反复")

        if latest > ma5 > ma20:
            score += 8
            reasons.append("最近几天的上升状态仍在延续")
        if 2 <= return20 <= 15:
            score += 8
            reasons.append(f"近一个月上涨约 {return20:.1f}%，速度不算极端")
        elif return20 > 20:
            score -= 12
            risks.append(f"近一个月已上涨约 {return20:.1f}%，短期追高风险较大")
        elif return20 < -10:
            score -= 10
            risks.append(f"近一个月下跌约 {abs(return20):.1f}%，尚未明显稳定")

        if -12 <= drawdown <= -3:
            score += 4
            reasons.append("距离近三个月最高点仍有一定空间")
        elif drawdown > -1:
            risks.append("价格接近近三个月高位，需要防止冲高回落")

        if volume_ratio is not None:
            if 1.15 <= volume_ratio <= 2.5:
                score += 7
                reasons.append("最新成交比近期平时更活跃")
            elif volume_ratio >= 3:
                score -= 4
                risks.append("成交突然放大很多，短期波动可能加剧")

        if volatility <= 2.5:
            score += 5
            reasons.append("近期每天的价格波动相对可控")
        elif volatility >= 5:
            score -= 8
            risks.append("近期每天涨跌幅度较大，新手模拟时也要谨慎")

        pe = _number(_first(snapshot, "市盈率-动态", "动态市盈率", "市盈率"))
        pb = _number(_first(snapshot, "市净率"))
        if pe is not None:
            facts.append(f"按当前利润计算的价格倍数约 {pe:.1f}")
            if 5 <= pe <= 45:
                score += 4
            elif pe > 70:
                score -= 5
                risks.append("按当前利润计算，价格水平偏高")
        if pb is not None:
            facts.append(f"股价相对公司净资产的倍数约 {pb:.1f}")

        if financial:
            revenue_growth = _number(
                _first(
                    financial,
                    "营业总收入同比增长",
                    "营业收入-同比增长",
                    "营业收入同比增长",
                )
            )
            profit_growth = _number(
                _first(
                    financial,
                    "净利润同比增长",
                    "净利润-同比增长",
                    "归属净利润同比增长",
                )
            )
            roe = _number(_first(financial, "净资产收益率", "加权净资产收益率"))
            if revenue_growth is not None:
                facts.append(f"最近一期营业收入同比 {revenue_growth:+.1f}%")
                if revenue_growth > 5:
                    score += 5
                    reasons.append("公司最近一期收入仍在增长")
                elif revenue_growth < -10:
                    score -= 7
                    risks.append("公司最近一期收入下降较明显")
            if profit_growth is not None:
                facts.append(f"最近一期净利润同比 {profit_growth:+.1f}%")
                if profit_growth > 5:
                    score += 7
                    reasons.append("公司最近一期利润仍在增长")
                elif profit_growth < -10:
                    score -= 10
                    risks.append("公司最近一期利润下降较明显")
            if roe is not None:
                facts.append(f"最近一期公司使用自有资本的盈利能力约 {roe:.1f}%")

        relevant_notices = notices[:5]
        for title in relevant_notices:
            if any(word in title for word in RISK_NOTICE_WORDS):
                score -= 8
                risks.append(f"近期公告需留意：{title[:34]}")
                break
        else:
            for title in relevant_notices:
                if any(word in title for word in POSITIVE_NOTICE_WORDS):
                    score += 4
                    reasons.append(f"近期公告出现积极事项：{title[:34]}")
                    break

        if not relevant_notices:
            facts.append("近期公告信息未取得或没有匹配记录")

        data_date = str(_first(history[-1], "日期", "date") or date.today())
        return StockAnalysis(
            code=code,
            name=self._snapshot_name(snapshot),
            score=max(0, min(100, round(score))),
            trend=trend,
            price=latest,
            data_date=data_date,
            reasons=reasons[:4] or ["目前没有足够明显的积极变化"],
            risks=risks[:3] or ["暂未发现突出的短期风险信号，但仍可能随市场变化"],
            facts=facts[:4],
        )

    def _analyze_code(
        self,
        code: str,
        snapshot: dict[str, Any],
        today: date,
        financials: dict[str, dict[str, Any]],
        notices: dict[str, list[str]],
    ) -> StockAnalysis:
        history = self.provider.history(code, today - timedelta(days=380), today)
        return self._analyze(
            code,
            snapshot,
            history,
            financials.get(code),
            notices.get(code, []),
        )

    def screen_market(self, today: date | None = None) -> list[StockAnalysis]:
        today = today or date.today()
        now = time.time()
        with self._lock:
            if self._market_cache and now - self._market_cache[0] < self.cache_seconds:
                return list(self._market_cache[1])

            snapshot = self._snapshot()
            eligible = [
                (code, row)
                for code, row in snapshot.items()
                if self._eligible_snapshot(row)
            ]
            eligible.sort(
                key=lambda item: self._preliminary_score(item[1]),
                reverse=True,
            )
            financials = self._financials(today)
            notices = self._notices(today)
            analyses: list[StockAnalysis] = []
            for code, row in eligible[:self.shortlist_size]:
                try:
                    analyses.append(
                        self._analyze_code(
                            code,
                            row,
                            today,
                            financials,
                            notices,
                        )
                    )
                except Exception:
                    continue
            analyses.sort(key=lambda item: item.score, reverse=True)
            self._market_cache = (now, analyses)
            return list(analyses)

    def analyze_codes(
        self,
        codes: list[str],
        today: date | None = None,
    ) -> tuple[list[StockAnalysis], list[str]]:
        today = today or date.today()
        snapshot = self._snapshot()
        financials = self._financials(today)
        notices = self._notices(today)
        analyses: list[StockAnalysis] = []
        errors: list[str] = []
        for code in codes[:5]:
            if code not in snapshot:
                errors.append(f"{code}：没有在当前 A 股代码表中找到")
                continue
            try:
                analyses.append(
                    self._analyze_code(
                        code,
                        snapshot[code],
                        today,
                        financials,
                        notices,
                    )
                )
            except Exception as exc:
                errors.append(f"{code}：{exc}")
        return analyses, errors

    def stock_identity(self, code: str) -> tuple[str, str] | None:
        normalized = normalize_stock_code(code)
        if not normalized:
            return None
        row = self._snapshot().get(normalized)
        if not row:
            return None
        return normalized, self._snapshot_name(row)

    def is_trading_day(self, target: date) -> bool:
        now = time.time()
        if not self._calendar_cache or now - self._calendar_cache[0] > 24 * 60 * 60:
            try:
                self._calendar_cache = (now, self.provider.trading_days())
            except Exception:
                return target.weekday() < 5
        return target in self._calendar_cache[1]

    @staticmethod
    def _format_item(item: StockAnalysis, index: int) -> str:
        lines = [
            f"{index}. {item.name}（{item.code}）",
            f"关注度：{item.score}/100｜趋势：{item.trend}｜收盘价：{item.price:.2f}",
            "为什么值得看：" + "；".join(item.reasons),
            "需要小心：" + "；".join(item.risks),
        ]
        if item.facts:
            lines.append("补充数据：" + "；".join(item.facts))
        return "\n".join(lines)

    def format_market_report(
        self,
        analyses: list[StockAnalysis],
        limit: int = 5,
    ) -> str:
        candidates = [item for item in analyses if item.score >= 65][:limit]
        if not candidates:
            return (
                "今日 A 股模拟关注报告\n\n"
                "今天没有筛出足够明确的新候选。少做一次判断，也比为了凑数随便挑股票更好。\n\n"
                f"数据来源：{self.provider.name}\n{DISCLAIMER}"
            )
        data_date = candidates[0].data_date
        lines = [
            f"A 股收盘模拟关注名单｜数据日期：{data_date}",
            "",
            "下面是今天较值得放进同花顺模拟盘观察的候选，已排除 ST、退市风险、"
            "成交太少、短期暴涨和明显亏损风险较高的股票。",
            "",
        ]
        for index, item in enumerate(candidates, 1):
            lines.extend([self._format_item(item, index), ""])
        lines.extend(
            [
                "怎么用：先把这些股票加入模拟盘观察，重点看报告中的风险项。"
                "模拟结果连续记录一段时间后，再判断这套筛选是否适合你。",
                "",
                f"数据来源：{self.provider.name}",
                DISCLAIMER,
            ]
        )
        return "\n".join(lines)

    def market_report(self, today: date | None = None, limit: int = 5) -> str:
        return self.format_market_report(self.screen_market(today), limit)

    def code_report(self, codes: list[str], today: date | None = None) -> str:
        analyses, errors = self.analyze_codes(codes, today)
        lines = ["A 股个股观察", ""]
        for index, item in enumerate(analyses, 1):
            lines.extend([self._format_item(item, index), ""])
        if errors:
            lines.append("未完成：" + "；".join(errors))
            lines.append("")
        lines.extend([f"数据来源：{self.provider.name}", DISCLAIMER])
        return "\n".join(lines)


def is_report_due(
    now: datetime,
    report_hour: int,
    report_minute: int,
    last_sent_date: str | None,
    is_trading_day: bool,
) -> bool:
    if not is_trading_day or last_sent_date == now.date().isoformat():
        return False
    return (now.hour, now.minute) >= (report_hour, report_minute)
