"""Preregistered Stage C1 daily factors and portfolio decision rules.

This module is offline-only. It has no import or write path to the forward
ledger and rejects every research or data request after 2024-12-31.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


RESEARCH_START = pd.Timestamp("2019-01-01")
RESEARCH_END = pd.Timestamp("2024-12-31")
PROHIBITED_START = pd.Timestamp("2025-01-01")
CANDIDATES = (
    "c1_weekly_rank",
    "c1_monthly_anchor_weekly_refill",
    "c1_daily_confirm_weekly_trade",
)
FACTORS = (
    "medium_relative_momentum",
    "short_industry_relative_strength",
    "low_volatility",
    "value_reasonableness",
    "liquidity_stability",
)
WEIGHTS = dict(zip(FACTORS, (0.30, 0.25, 0.20, 0.15, 0.10)))


def guard_research_interval(start: Any, end: Any) -> tuple[pd.Timestamp, pd.Timestamp]:
    left, right = pd.Timestamp(start), pd.Timestamp(end)
    if left < RESEARCH_START or right > RESEARCH_END or right < left:
        raise ValueError("C1 permits only 2019-01-01 through 2024-12-31; audit data is forbidden")
    return left, right


def guard_data_request(start: Any, end: Any) -> tuple[pd.Timestamp, pd.Timestamp]:
    left, right = pd.Timestamp(start), pd.Timestamp(end)
    if right >= PROHIBITED_START or right < left:
        raise ValueError("C1 data request crossed the 2025 audit firewall")
    return left, right


def load_c1_config(path: str | Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    guard_research_interval(config["research_start"], config["research_end"])
    if config.get("prohibited_audit_start") != "2025-01-01":
        raise ValueError("C1 audit boundary changed")
    if config.get("strategy_name") != "hybrid_weekly_challenger_v1":
        raise ValueError("C1 strategy identity changed")
    if config.get("factors") != WEIGHTS:
        raise ValueError("C1 preregistered factor weights changed")
    if tuple(item.get("candidate_id") for item in config.get("candidates", ())) != CANDIDATES:
        raise ValueError("C1 permits exactly N1, N2 and N3")
    portfolio = config.get("portfolio", {})
    if (portfolio.get("top_k"), portfolio.get("maximum_per_industry"), portfolio.get("target_weight")) != (5, 2, 0.20):
        raise ValueError("C1 frozen portfolio constraints changed")
    if tuple(config.get("cost_stress_multipliers", ())) != (1.0, 1.5, 2.0):
        raise ValueError("C1 cost stress grid changed")
    return config


def _winsor_z(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = numeric.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=values.index, dtype=float)
    clipped = numeric.clip(valid.quantile(0.05), valid.quantile(0.95))
    std = float(clipped.std(ddof=0))
    if not math.isfinite(std) or std == 0:
        return pd.Series(np.where(clipped.notna(), 0.0, np.nan), index=values.index, dtype=float)
    return (clipped - float(clipped.mean())) / std


def score_cross_section(
    frame: pd.DataFrame, benchmark_return20: float,
    benchmark_return126_21: float = 0.0,
) -> pd.DataFrame:
    """Score one close-of-day cross-section without filling missing factors."""
    required = {
        "code", "industry", "return126_21", "return20", "volatility60",
        "pe_ttm", "pb_mrq", "turn_mean20", "turn_std20", "amount20",
        "valid_days60", "tradestatus", "is_st", "close",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"C1 cross-section missing {sorted(missing)}")
    data = frame.copy().sort_values("code", kind="mergesort").set_index("code", drop=False)
    data.index.name = None
    amount_floor = float(pd.to_numeric(data["amount20"], errors="coerce").quantile(0.20))
    eligible = (
        (pd.to_numeric(data["close"], errors="coerce") > 0)
        & (pd.to_numeric(data["tradestatus"], errors="coerce") == 1)
        & (pd.to_numeric(data["is_st"], errors="coerce") == 0)
        & (pd.to_numeric(data["valid_days60"], errors="coerce") >= 55)
        & (pd.to_numeric(data["amount20"], errors="coerce") >= amount_floor)
    )
    data["eligible_base"] = eligible
    industry_median = data.loc[eligible].groupby("industry")["return20"].median()
    industry_count = data.loc[eligible].groupby("industry")["return20"].count()
    peer = data["industry"].map(industry_median)
    fallback = data["industry"].map(industry_count).fillna(0) < 5
    peer = peer.where(~fallback, float(benchmark_return20))
    data["industry_fallback"] = fallback
    data["medium_relative_momentum"] = pd.to_numeric(data["return126_21"], errors="coerce") - float(benchmark_return126_21)
    data["short_industry_relative_strength"] = pd.to_numeric(data["return20"], errors="coerce") - peer
    data["low_volatility"] = -pd.to_numeric(data["volatility60"], errors="coerce")
    pe = pd.to_numeric(data["pe_ttm"], errors="coerce")
    pb = pd.to_numeric(data["pb_mrq"], errors="coerce")
    data["value_reasonableness"] = (-0.65 * np.log(pe) - 0.35 * np.log(pb)).where((pe > 0) & (pb > 0))
    turn_mean_z = _winsor_z(data["turn_mean20"])
    turn_std_z = _winsor_z(data["turn_std20"])
    data["liquidity_stability"] = 0.6 * turn_mean_z - 0.4 * turn_std_z
    standardized = pd.DataFrame({factor: _winsor_z(data.loc[eligible, factor]) for factor in FACTORS})
    data["valid_factor_count"] = standardized.notna().sum(axis=1).reindex(data.index).fillna(0).astype(int)
    weighted = standardized.mul(pd.Series(WEIGHTS))
    denominator = standardized.notna().mul(pd.Series(WEIGHTS)).sum(axis=1)
    data["score"] = weighted.sum(axis=1, min_count=1).div(denominator).where(denominator > 0)
    data["eligible"] = eligible & (data["valid_factor_count"] >= 4)
    ordered = data.loc[data["eligible"]].sort_values(["score", "code"], ascending=[False, True], kind="mergesort")
    rank = pd.Series(np.arange(1, len(ordered) + 1), index=ordered.index)
    data["rank"] = rank.reindex(data.index).astype("Int64")
    return data.reset_index(drop=True)


def last_trading_day_of_week(days: Sequence[pd.Timestamp]) -> set[pd.Timestamp]:
    index = pd.DatetimeIndex(pd.to_datetime(days)).sort_values()
    groups = pd.Series(index=index, data=index).groupby(index.to_period("W-FRI"))
    return {pd.Timestamp(group.max()) for _, group in groups}


def last_trading_day_of_month(days: Sequence[pd.Timestamp]) -> set[pd.Timestamp]:
    index = pd.DatetimeIndex(pd.to_datetime(days)).sort_values()
    groups = pd.Series(index=index, data=index).groupby(index.to_period("M"))
    return {pd.Timestamp(group.max()) for _, group in groups}


def choose_with_industry_cap(
    ranked_codes: Sequence[str], industries: Mapping[str, str], limit: int = 5,
    maximum_per_industry: int = 2, preferred: Iterable[str] = (),
) -> tuple[str, ...]:
    selected: list[str] = []
    counts: dict[str, int] = {}
    ordered = list(dict.fromkeys([*preferred, *ranked_codes]))
    for code in ordered:
        industry = str(industries.get(code, f"unclassified:{code}"))
        if counts.get(industry, 0) >= maximum_per_industry:
            continue
        selected.append(code)
        counts[industry] = counts.get(industry, 0) + 1
        if len(selected) >= limit:
            break
    return tuple(selected)


@dataclass(frozen=True)
class Decision:
    target_codes: tuple[str, ...]
    ordinary_sells: tuple[str, ...]
    hard_sells: tuple[str, ...]


class CandidateState:
    def __init__(self, candidate_id: str):
        if candidate_id not in CANDIDATES:
            raise ValueError(f"Unregistered C1 candidate: {candidate_id}")
        self.candidate_id = candidate_id
        self.top5_streak: dict[str, int] = {}
        self.out15_streak: dict[str, int] = {}

    def observe(self, ranks: Mapping[str, int]) -> None:
        for code in set(self.top5_streak) | set(ranks):
            self.top5_streak[code] = self.top5_streak.get(code, 0) + 1 if ranks.get(code, 10**9) <= 5 else 0
            self.out15_streak[code] = self.out15_streak.get(code, 0) + 1 if ranks.get(code, 10**9) > 15 else 0

    def decide(
        self, held: Iterable[str], ranked_codes: Sequence[str], ranks: Mapping[str, int],
        industries: Mapping[str, str], *, weekly: bool, month_end: bool,
        hard_exits: Iterable[str] = (),
    ) -> Decision:
        held_set, hard = set(held), set(hard_exits) & set(held)
        survivors = held_set - hard
        ordinary: list[str] = []
        if self.candidate_id == "c1_weekly_rank" and weekly:
            ordinary = sorted((code for code in survivors if ranks.get(code, 10**9) > 12), key=lambda c: (-ranks.get(c, 10**9), c))[:1]
        elif self.candidate_id == "c1_monthly_anchor_weekly_refill" and month_end:
            desired = set(choose_with_industry_cap(ranked_codes[:5], industries))
            ordinary = sorted(survivors - desired)
        elif self.candidate_id == "c1_daily_confirm_weekly_trade" and weekly:
            ordinary = sorted((code for code in survivors if self.out15_streak.get(code, 0) >= 3), key=lambda c: (-ranks.get(c, 10**9), c))[:1]
        survivors -= set(ordinary)
        can_rebalance = weekly or (self.candidate_id == "c1_monthly_anchor_weekly_refill" and month_end)
        if not can_rebalance:
            return Decision(tuple(sorted(survivors)), tuple(ordinary), tuple(sorted(hard)))
        eligible_buys = list(ranked_codes[:5])
        if self.candidate_id == "c1_daily_confirm_weekly_trade":
            eligible_buys = [code for code in eligible_buys if self.top5_streak.get(code, 0) >= 3]
        target = choose_with_industry_cap(eligible_buys, industries, preferred=sorted(survivors, key=lambda c: ranks.get(c, 10**9)))
        return Decision(target, tuple(ordinary), tuple(sorted(hard)))


def r2_condition(row: Mapping[str, Any]) -> bool:
    return (
        float(row.get("return20", math.inf)) <= -0.12
        and float(row.get("ma60_gap", math.inf)) <= -0.03
        and float(row.get("relative20", math.inf)) <= -0.08
    )


def hard_gate_results(metrics: pd.DataFrame) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for row in metrics.to_dict("records"):
        checks = {
            "annual_return": row["annual_return"] >= 0.15,
            "annual_excess": row["annual_excess_return"] >= 0.06,
            "max_drawdown": row["max_drawdown"] >= -0.23,
            "positive_years": row["positive_years"] >= 4,
            "excess_years": row["excess_years"] >= 3,
            "turnover": row["annual_two_way_turnover"] <= 12.0,
            "holding_days": row["average_holding_days"] >= 20,
            "same_day_round_trips": row["same_day_round_trips"] == 0,
            "dynamic_2x_return": row["dynamic_2x_annual_return"] > 0,
            "dynamic_2x_drawdown": row["dynamic_2x_max_drawdown"] >= -0.27,
        }
        results[str(row["candidate_id"])] = {"passed": all(checks.values()), "checks": checks}
    return results


def build_daily_score_panel(backtester: Any, benchmark_levels: pd.Series) -> pd.DataFrame:
    """Build bounded point-in-time daily scores for 2019-2024 only."""
    guard_data_request(backtester.history_start, backtester.end)
    prices = pd.concat(
        [frame.assign(code=code).reset_index() for code, frame in backtester.by_code.items() if code != "sh.000300"],
        ignore_index=True,
    ).sort_values(["code", "date"], kind="mergesort")
    prices = prices[prices["date"] <= RESEARCH_END].copy()
    grouped = prices.groupby("code", sort=False)
    close = pd.to_numeric(prices["close"], errors="coerce")
    prices["return20"] = close / grouped["close"].shift(20) - 1
    prices["return126_21"] = grouped["close"].shift(21) / grouped["close"].shift(126) - 1
    daily_return = grouped["close"].pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    prices["volatility60"] = daily_return.groupby(prices["code"]).transform(lambda value: value.rolling(60, min_periods=60).std(ddof=0))
    prices["turn_mean20"] = grouped["turn"].transform(lambda value: value.rolling(20, min_periods=20).mean())
    prices["turn_std20"] = grouped["turn"].transform(lambda value: value.rolling(20, min_periods=20).std(ddof=0))
    prices["amount20"] = grouped["amount"].transform(lambda value: value.rolling(20, min_periods=20).mean())
    prices["ma60"] = grouped["close"].transform(lambda value: value.rolling(60, min_periods=60).mean())
    valid = ((prices["tradestatus"] == 1) & (prices["close"] > 0)).astype(int)
    prices["valid_days60"] = valid.groupby(prices["code"]).transform(lambda value: value.rolling(60, min_periods=60).sum())
    by_date = {pd.Timestamp(day): group for day, group in prices.groupby("date", sort=True)}

    levels = benchmark_levels.sort_index().loc[:RESEARCH_END]
    bench20 = levels / levels.shift(20) - 1
    bench126_21 = levels.shift(21) / levels.shift(126) - 1
    membership_dates = [pd.Timestamp(value) for value in backtester.cache.membership_dates("2017-01-01", "2024-12-31")]
    if not membership_dates:
        raise RuntimeError("C1 has no point-in-time CSI 300 snapshots")
    output: list[pd.DataFrame] = []
    for day in backtester.trading_days:
        guard_research_interval(day, day)
        snapshots = [value for value in membership_dates if value <= day]
        if not snapshots or day not in by_date or day not in levels.index:
            continue
        snapshot = snapshots[-1]
        metadata = backtester.cache.candidate_pool_metadata(snapshot.date().isoformat(), expected_count=300)
        members = set(metadata["codes"])
        cross = by_date[day]
        cross = cross[cross["code"].isin(members)].copy()
        if cross.empty:
            continue
        industries = backtester.cache.industries(snapshot.date().isoformat())
        cross["industry"] = cross["code"].map(industries)
        scored = score_cross_section(
            cross,
            float(bench20.get(day, np.nan)),
            float(bench126_21.get(day, np.nan)),
        )
        scored["date"] = day
        scored["membership_snapshot"] = snapshot
        scored["candidate_pool_sha256"] = metadata["sha256"]
        scored["candidate_pool_source_date"] = metadata["source_date"]
        scored["relative20"] = scored["return20"] - float(bench20.get(day, np.nan))
        scored["ma60_gap"] = scored["close"] / scored["ma60"] - 1
        output.append(scored)
    if not output:
        raise RuntimeError("C1 produced no daily point-in-time scores")
    panel = pd.concat(output, ignore_index=True)
    guard_research_interval(panel["date"].min(), panel["date"].max())
    return panel.sort_values(["date", "rank", "code"], kind="mergesort", na_position="last").reset_index(drop=True)


def _fill_payload(fill: Any) -> dict[str, Any]:
    return {
        "fill_id": fill.fill_id, "order_id": fill.order_id, "code": fill.code,
        "side": fill.side.value, "quantity": fill.quantity,
        "trade_date": fill.trade_date.isoformat(), "reference_price": fill.reference_price,
        "execution_price": fill.execution_price, "notional": fill.notional,
        "costs": fill.costs.as_dict(),
    }


def run_c1_candidate(
    backtester: Any, panel: pd.DataFrame, candidate_id: str, cost_model: Any | None = None,
) -> pd.DataFrame:
    """Execute one preregistered C1 candidate on the shared order model."""
    from execution_model import OrderSide, OrderStatus, Portfolio
    from low_turnover_b1 import _target_quantities, delta_orders, execute_delta_orders

    if candidate_id not in CANDIDATES:
        raise ValueError(f"Unregistered C1 candidate: {candidate_id}")
    dates = pd.to_datetime(panel["date"], errors="raise")
    guard_research_interval(dates.min(), dates.max())
    model = cost_model or backtester.cost_model
    portfolio = Portfolio(cash=backtester.initial_capital, cost_model=model)
    state = CandidateState(candidate_id)
    weekly_days = last_trading_day_of_week(backtester.trading_days)
    monthly_days = last_trading_day_of_month(backtester.trading_days)
    panel_by_day = {pd.Timestamp(day): frame.set_index("code", drop=False) for day, frame in panel.groupby("date", sort=True)}
    planned: dict[pd.Timestamp, dict[str, Any]] = {}
    r2_streak: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    risk_exits: list[dict[str, Any]] = []
    terminal_notionals: dict[str, list[float]] = {}

    days = list(backtester.trading_days)
    next_day = {day: days[index + 1] for index, day in enumerate(days[:-1])}
    for day in days:
        day = pd.Timestamp(day)
        first_fill, first_event = len(portfolio.fills), len(portfolio.events)
        start_equity = float(records[-1]["equity"]) if records else float(backtester.initial_capital)
        for pending in list(portfolio.pending_orders.values()):
            if pending.side == OrderSide.BUY and day in planned:
                portfolio.cancel_order(pending.order_id, "new_signal_window", day.date())
        pending_codes = [item.code for item in portfolio.pending_orders.values()]
        portfolio.retry_pending(day.date(), backtester._market_for_codes(pending_codes, day))

        plan = planned.get(day)
        if plan is not None:
            target_codes = tuple(plan["target_codes"])
            target_quantities = _target_quantities(backtester, portfolio, day, target_codes, 1.0)
            current = {code: position.quantity for code, position in portfolio.positions.items()}
            pending_sells = {item.code for item in portfolio.pending_orders.values() if item.side == OrderSide.SELL}
            instructions = list(delta_orders(current, target_quantities, pending_sells))
            instructions.sort(key=lambda item: (0 if item.side == "sell" else 1, item.code))
            retry_fills = portfolio.fills[first_fill:]
            opposite = {item.code for item in instructions for fill in retry_fills if fill.code == item.code and fill.side.value != item.side}
            submitted = execute_delta_orders(
                portfolio, instructions,
                backtester._market_for_codes((item.code for item in instructions), day),
                day.date(), f"{candidate_id}:{day.date().isoformat()}", opposite,
            )
            for order in submitted:
                orders.append({
                    "candidate_id": candidate_id, "signal_date": plan["signal_date"],
                    "execution_date": day, "order_id": order.order_id, "code": order.code,
                    "side": order.side.value, "quantity": order.quantity,
                    "status_after_submission": order.status.value,
                    "reason_after_submission": order.reason,
                })

        cross = panel_by_day.get(day)
        if cross is not None:
            eligible = cross[cross["eligible"] & cross["rank"].notna()].sort_values(["rank", "code"], kind="mergesort")
            ranked = tuple(eligible["code"].astype(str))
            ranks = {str(code): int(rank) for code, rank in zip(eligible["code"], eligible["rank"])}
            industries = {str(code): str(industry) for code, industry in zip(cross["code"], cross["industry"])}
            state.observe(ranks)
            held = set(portfolio.positions)
            hard: set[str] = set()
            for code in held:
                if code not in cross.index or not bool(cross.at[code, "eligible_base"]):
                    hard.add(code)
                    risk_exits.append({"candidate_id": candidate_id, "signal_date": day, "code": code, "risk_type": "R1", "triggered": True})
                    continue
                condition = r2_condition(cross.loc[code])
                r2_streak[code] = r2_streak.get(code, 0) + 1 if condition else 0
                if r2_streak[code] >= 2:
                    hard.add(code)
                    risk_exits.append({"candidate_id": candidate_id, "signal_date": day, "code": code, "risk_type": "R2", "triggered": True})
            weekly, month_end = day in weekly_days, day in monthly_days
            should_plan = bool(hard) or weekly or (candidate_id == "c1_monthly_anchor_weekly_refill" and month_end)
            if should_plan and day in next_day:
                decision = state.decide(held, ranked, ranks, industries, weekly=weekly, month_end=month_end, hard_exits=hard)
                planned[next_day[day]] = {
                    "signal_date": day, "target_codes": decision.target_codes,
                    "ordinary_sells": decision.ordinary_sells, "hard_sells": decision.hard_sells,
                }
                signals.append({
                    "candidate_id": candidate_id, "signal_date": day,
                    "execution_date": next_day[day], "target_codes": ",".join(decision.target_codes),
                    "ordinary_sell_count": len(decision.ordinary_sells),
                    "hard_sell_count": len(decision.hard_sells), "weekly": weekly,
                    "month_end": month_end,
                })

        closing = {}
        for code in portfolio.positions:
            bar = backtester._market_bar(code, day)
            if bar is not None:
                closing[code] = bar.close
        equity = portfolio.mark_to_market(closing)
        terminal = portfolio.terminal_valuation(day.date(), closing)
        terminal_notionals[day.date().isoformat()] = [position.quantity * position.last_price for position in portfolio.positions.values()]
        day_fills = portfolio.fills[first_fill:]
        buys = sum(fill.reference_price * fill.quantity for fill in day_fills if fill.side == OrderSide.BUY)
        sells = sum(fill.reference_price * fill.quantity for fill in day_fills if fill.side == OrderSide.SELL)
        events = portfolio.events[first_event:]
        records.append({
            "date": day, "equity": equity, "cash": portfolio.cash,
            "market_value": equity - portfolio.cash,
            "hypothetical_liquidation_equity": terminal.hypothetical_liquidation_equity,
            "hypothetical_liquidation_cost": terminal.hypothetical_liquidation_cost,
            "return": equity / start_equity - 1 if start_equity else 0.0,
            "turnover_notional": buys + sells, "net_turnover_notional": abs(buys - sells),
            "transaction_cost": sum(fill.costs.total for fill in day_fills),
            "positions": len(portfolio.positions), "pending_orders": len(portfolio.pending_orders),
            "unfilled_attempts": sum(item.get("status") in {OrderStatus.PENDING.value, OrderStatus.SUSPENDED.value, OrderStatus.LIMIT_LOCKED.value} for item in events),
        })
        for code, position in sorted(portfolio.positions.items()):
            holdings.append({
                "candidate_id": candidate_id, "date": day, "code": code,
                "quantity": position.quantity, "last_price": position.last_price,
                "market_value": position.market_value,
                "weight": position.market_value / equity if equity else 0.0,
                "industry": str(cross.at[code, "industry"]) if cross is not None and code in cross.index else "unknown",
            })

    result = pd.DataFrame(records).set_index("date")
    fills = [_fill_payload(fill) for fill in portfolio.fills]
    grouped_sides: dict[tuple[str, str], set[str]] = {}
    for fill in fills:
        grouped_sides.setdefault((fill["trade_date"], fill["code"]), set()).add(fill["side"])
    if any(sides == {"buy", "sell"} for sides in grouped_sides.values()):
        raise RuntimeError("C1 produced a same-security same-day round trip")
    result.attrs.update({
        "initial_capital": backtester.initial_capital, "candidate_id": candidate_id,
        "cost_model_version": model.version, "fills": fills,
        "order_events": list(portfolio.events), "signals": signals,
        "submitted_orders": orders, "holdings": holdings, "risk_exits": risk_exits,
        "terminal_reference_notionals": terminal_notionals,
        "terminal_positions": {code: {"quantity": p.quantity, "average_cost": p.average_cost, "last_price": p.last_price} for code, p in portfolio.positions.items()},
    })
    return result
