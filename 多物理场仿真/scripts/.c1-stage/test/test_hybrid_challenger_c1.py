from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from hybrid_challenger_c1 import (  # noqa: E402
    CandidateState, choose_with_industry_cap, guard_data_request,
    last_trading_day_of_week, r2_condition, score_cross_section,
)


def test_audit_period_is_rejected():
    with pytest.raises(ValueError, match="audit firewall"):
        guard_data_request("2019-01-01", "2025-01-01")


def test_holiday_shortened_week_uses_actual_last_trading_day():
    days = pd.to_datetime(["2024-09-23", "2024-09-24", "2024-09-25", "2024-09-26"])
    assert last_trading_day_of_week(days) == {pd.Timestamp("2024-09-26")}


def test_n1_rank_12_retained_rank_13_sold_and_only_one_replacement():
    state = CandidateState("c1_weekly_rank")
    held = ["a", "b", "c"]
    ranked = ["x", "y", "z", "q", "r", "a", "b"]
    ranks = {"a": 12, "b": 13, "c": 20, **{code: i for i, code in enumerate(ranked[:5], 1)}}
    decision = state.decide(held, ranked, ranks, {code: code for code in held + ranked}, weekly=True, month_end=False)
    assert "a" in decision.target_codes
    assert len(decision.ordinary_sells) == 1
    assert decision.ordinary_sells == ("c",)


def test_n2_does_not_sell_for_midmonth_rank_change():
    state = CandidateState("c1_monthly_anchor_weekly_refill")
    decision = state.decide(["held"], ["new"], {"held": 100, "new": 1}, {"held": "i", "new": "j"}, weekly=True, month_end=False)
    assert decision.ordinary_sells == ()
    assert "held" in decision.target_codes


def test_n3_requires_three_consecutive_observations():
    state = CandidateState("c1_daily_confirm_weekly_trade")
    for _ in range(2):
        state.observe({"new": 1, "held": 16})
    early = state.decide(["held"], ["new"], {"new": 1, "held": 16}, {"new": "a", "held": "b"}, weekly=True, month_end=False)
    assert "new" not in early.target_codes
    state.observe({"new": 1, "held": 16})
    ready = state.decide(["held"], ["new"], {"new": 1, "held": 16}, {"new": "a", "held": "b"}, weekly=True, month_end=False)
    assert "new" in ready.target_codes
    assert ready.ordinary_sells == ("held",)


def test_hard_exit_overrides_buffer():
    state = CandidateState("c1_weekly_rank")
    decision = state.decide(["held"], ["held"], {"held": 1}, {"held": "a"}, weekly=False, month_end=False, hard_exits=["held"])
    assert decision.hard_sells == ("held",)
    assert "held" not in decision.target_codes


def test_r2_needs_all_three_conditions():
    assert r2_condition({"return20": -0.13, "ma60_gap": -0.04, "relative20": -0.09})
    assert not r2_condition({"return20": -0.13, "ma60_gap": -0.02, "relative20": -0.09})


def test_industry_cap_is_two():
    ranked = ["a", "b", "c", "d", "e", "f"]
    industries = {"a": "x", "b": "x", "c": "x", "d": "y", "e": "z", "f": "q"}
    selected = choose_with_industry_cap(ranked, industries)
    assert len(selected) == 5
    assert sum(industries[code] == "x" for code in selected) == 2


def test_score_missing_value_factor_reweights_without_future_fill():
    rows = []
    for i in range(10):
        rows.append({
            "code": f"c{i}", "industry": "x" if i < 5 else "y",
            "return126_21": i / 100, "return20": i / 200,
            "volatility60": 0.02 + i / 1000, "pe_ttm": None if i == 9 else 10 + i,
            "pb_mrq": None if i == 9 else 1 + i / 10, "turn_mean20": 1 + i / 10,
            "turn_std20": 0.1 + i / 100, "amount20": 1e8 + i * 1e7,
            "valid_days60": 60, "tradestatus": 1, "is_st": 0, "close": 10,
        })
    scored = score_cross_section(pd.DataFrame(rows), 0.0)
    last = scored.set_index("code").loc["c9"]
    assert last["valid_factor_count"] == 4
    assert pd.notna(last["score"])
