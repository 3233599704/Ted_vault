import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from paper_portfolio import PaperPortfolioStore
from stock_research import (
    EastmoneyPublicProvider,
    StockResearchService,
    TencentCsi300Provider,
    normalize_stock_code,
)


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class FastProvider:
    name = "fast-test"

    def __init__(self):
        self.quote_calls = 0

    def quotes(self, codes):
        self.quote_calls += 1
        return [{
            "代码": codes[0],
            "名称": "测试股份",
            "最新价": 18.9,
            "涨跌幅": 1.2,
            "成交额": 800_000_000,
            "量比": 1.2,
        }]

    def market_snapshot(self):
        raise AssertionError("single-code analysis must not fetch the full market")

    def history(self, _code, _start, _end):
        first = date(2026, 1, 1)
        return [
            {"日期": (first + timedelta(days=index)).isoformat(), "收盘": 10 + index * 0.1, "成交量": 1_000_000}
            for index in range(90)
        ]

    def latest_financials(self, _today):
        return {}

    def recent_notices(self, _today, _days=7):
        return {}


class FastQuoteTests(unittest.TestCase):
    def test_exchange_traded_fund_code_is_supported(self):
        self.assertEqual(normalize_stock_code("159018"), "159018")
        self.assertEqual(TencentCsi300Provider._symbol("159018"), "sz159018")

    def test_sina_quote_parser(self):
        body = (
            'var hq_str_sh600519="贵州茅台,1180.00,1170.00,1182.19,1190.00,'
            '1160.00,0,0,123456,987654321,2026-07-10,15:00:00";\n'
        ).encode("gb18030")
        with patch("stock_research.urllib.request.urlopen", return_value=FakeResponse(body)):
            rows = EastmoneyPublicProvider().quotes(["600519"])
        self.assertEqual(rows[0]["代码"], "600519")
        self.assertEqual(rows[0]["名称"], "贵州茅台")
        self.assertAlmostEqual(rows[0]["最新价"], 1182.19)

    def test_single_code_analysis_skips_full_market_snapshot(self):
        provider = FastProvider()
        service = StockResearchService(provider=provider, minimum_market_size=1)
        report = service.code_report(["600001"], date(2026, 7, 10))
        self.assertIn("测试股份", report)
        self.assertEqual(provider.quote_calls, 1)

    def test_new_etf_can_be_analyzed_with_more_than_twenty_days(self):
        provider = FastProvider()
        original_history = provider.history
        provider.history = lambda code, start, end: original_history(code, start, end)[-45:]
        service = StockResearchService(provider=provider, minimum_market_size=1)
        report = service.code_report(["159018"], date(2026, 7, 10))
        self.assertIn("测试股份", report)
        self.assertIn("上市历史较短", report)

    def test_tencent_quote_parser_maps_units_and_valuation(self):
        fields = [""] * 100
        fields[1] = "测试股份"
        fields[2] = "600001"
        fields[3] = "18.90"
        fields[4] = "18.50"
        fields[32] = "2.16"
        fields[36] = "12345"
        fields[37] = "98765.4"
        fields[38] = "1.80"
        fields[39] = "22.50"
        fields[44] = "520.25"
        fields[45] = "410.10"
        fields[46] = "2.40"
        fields[49] = "1.35"
        body = f'v_sh600001="{"~".join(fields)}";'
        provider = TencentCsi300Provider()
        with patch.object(provider, "_request_text", return_value=body):
            row = provider.quotes(["600001"])[0]
        self.assertEqual(row["名称"], "测试股份")
        self.assertEqual(row["成交额"], 987_654_000)
        self.assertEqual(row["总市值"], 52_025_000_000)
        self.assertAlmostEqual(row["市盈率-动态"], 22.5)

    def test_theme_candidates_use_live_quote_filters(self):
        provider = FastProvider()
        provider.quotes = lambda codes: [
            {
                "代码": code,
                "名称": f"候选{index}",
                "最新价": 15 + index,
                "涨跌幅": 0.5,
                "成交额": 900_000_000 - index * 10_000_000,
                "换手率": 2,
                "市盈率-动态": 20,
                "市净率": 2,
                "总市值": 50_000_000_000,
                "60日涨跌幅": 10,
            }
            for index, code in enumerate(codes)
        ]
        service = StockResearchService(provider=provider, minimum_market_size=1)
        grouped = service.theme_candidates(["AI能源与电力"], limit_per_theme=3)
        self.assertEqual(len(grouped["AI能源与电力"]), 3)
        self.assertTrue(all(item["code"] for item in grouped["AI能源与电力"]))

    def test_watchlist_scans_beyond_the_first_five_codes(self):
        provider = FastProvider()
        provider.quotes = lambda codes: [
            {
                "代码": code,
                "名称": f"观察{index}",
                "最新价": 18,
                "涨跌幅": 5.5 if index == len(codes) - 1 else 0.2,
                "成交额": 800_000_000,
                "换手率": 2,
                "市盈率-动态": 20,
                "市净率": 2,
            }
            for index, code in enumerate(codes)
        ]
        service = StockResearchService(provider=provider, minimum_market_size=1)
        codes = ["600001", "600002", "600003", "600004", "600005", "600006"]
        prioritized = service._watchlist_priority_codes(codes, limit=3)
        self.assertIn("600006", prioritized)

    def test_short_candidate_list_limits_each_industry_to_one(self):
        analyses = [
            SimpleNamespace(code="600001", metrics={"industry": "J66货币金融服务"}),
            SimpleNamespace(code="600002", metrics={"industry": "J66货币金融服务"}),
            SimpleNamespace(code="600003", metrics={"industry": "C39计算机通信制造"}),
            SimpleNamespace(code="600004", metrics={"industry": "D44电力热力生产"}),
        ]
        selected = StockResearchService._diversified_candidates(analyses, 3)
        self.assertEqual([item.code for item in selected], ["600001", "600003", "600004"])


class FactorProvider:
    name = "factor-test"

    def market_snapshot(self):
        return [
            {"代码": "600001", "名称": "稳健股份", "最新价": 28, "涨跌幅": 0.5,
             "成交额": 1_500_000_000, "换手率": 2, "市盈率-动态": 20, "市净率": 2.2,
             "总市值": 50_000_000_000, "60日涨跌幅": 15},
            {"代码": "600002", "名称": "震荡股份", "最新价": 18, "涨跌幅": -0.5,
             "成交额": 900_000_000, "换手率": 3, "市盈率-动态": 35, "市净率": 4,
             "总市值": 30_000_000_000, "60日涨跌幅": 2},
            {"代码": "600003", "名称": "走弱股份", "最新价": 12, "涨跌幅": -1,
             "成交额": 700_000_000, "换手率": 2, "市盈率-动态": 50, "市净率": 5,
             "总市值": 20_000_000_000, "60日涨跌幅": -12},
        ]

    def quotes(self, codes):
        rows = {row["代码"]: row for row in self.market_snapshot()}
        return [rows[code] for code in codes if code in rows]

    def history(self, code, _start, _end):
        first = date(2025, 10, 1)
        rows = []
        for index in range(180):
            if code == "600001":
                close = 12 + index * 0.09
            elif code == "600002":
                close = 18 + ((index % 10) - 5) * 0.15
            else:
                close = 24 - index * 0.065
            rows.append({
                "日期": (first + timedelta(days=index)).isoformat(),
                "开盘": close * 0.995,
                "收盘": close,
                "最高": close * 1.015,
                "最低": close * 0.985,
                "成交量": 1_000_000 + index * 1000,
            })
        return rows

    def latest_financials(self, _today):
        return {
            "600001": {"营业总收入同比增长": 12, "净利润同比增长": 18, "净资产收益率": 15},
            "600002": {"营业总收入同比增长": 2, "净利润同比增长": 1, "净资产收益率": 7},
            "600003": {"营业总收入同比增长": -8, "净利润同比增长": -15, "净资产收益率": 3},
        }

    def recent_notices(self, _today, _days=7):
        return {}

    def trading_days(self):
        return {date(2026, 7, 10)}


class PaperPortfolioTests(unittest.TestCase):
    def test_full_close_removes_position_and_later_settles_estimated_price(self):
        store = PaperPortfolioStore("unused-paper.json")
        state_data = {"version": 1, "users": {}}
        with patch.object(store, "_load_unlocked", return_value=state_data), patch(
            "paper_portfolio._atomic_write_json"
        ):
            store.import_snapshot(
                "user",
                100_000,
                50_000,
                [
                    {"code": "605116", "name": "奥锐特", "quantity": 1000, "avg_cost": 19.0},
                    {"code": "300579", "name": "数字认证", "quantity": 200, "avg_cost": 44.0},
                ],
            )
            state, trade = store.close_position("user", "奥锐特已经卖完了")
            self.assertNotIn("605116", state["positions"])
            self.assertIn("300579", state["positions"])
            self.assertTrue(trade["estimated_price"])
            self.assertAlmostEqual(state["cash"], 69_000)

            state, trade = store.settle_estimated_close(
                "user", "奥锐特实际成交价20.5", 20.5
            )
            self.assertFalse(trade["estimated_price"])
            self.assertAlmostEqual(state["cash"], 70_500)
            self.assertAlmostEqual(state["realized_pnl"], 1_500)

    def test_full_close_with_price_updates_cash_and_realized_pnl_immediately(self):
        store = PaperPortfolioStore("unused-paper.json")
        state_data = {"version": 1, "users": {}}
        with patch.object(store, "_load_unlocked", return_value=state_data), patch(
            "paper_portfolio._atomic_write_json"
        ):
            store.import_snapshot(
                "user",
                100_000,
                50_000,
                [{"code": "300579", "name": "数字认证", "quantity": 200, "avg_cost": 44.0}],
            )
            state, trade = store.close_position(
                "user", "数字认证全部卖出了", 20.9
            )
            self.assertEqual(state["positions"], {})
            self.assertAlmostEqual(state["cash"], 54_180)
            self.assertAlmostEqual(state["realized_pnl"], -4_620)
            self.assertFalse(trade["estimated_price"])

    def test_import_snapshot_replaces_positions_with_verified_costs(self):
        store = PaperPortfolioStore("unused-paper.json")
        state_data = {"version": 1, "users": {}}
        with patch.object(store, "_load_unlocked", return_value=state_data), patch(
            "paper_portfolio._atomic_write_json"
        ):
            state = store.import_snapshot(
                "user",
                199_312.72,
                55_487.11,
                [
                    {"code": "605116", "name": "奥锐特", "quantity": 2800, "avg_cost": 19.0452},
                    {"code": "159018", "name": "石油ETF广发", "quantity": 9900, "avg_cost": 0.9723},
                ],
                "2026-07-10 持仓截图",
            )
        self.assertEqual(set(state["positions"]), {"605116", "159018"})
        self.assertAlmostEqual(state["cash"], 55_487.11)
        self.assertFalse(state["capital_is_default"])
        self.assertEqual(state["trades"][-1]["side"], "snapshot_import")

    def test_ledger_uses_weighted_cost_and_realized_pnl(self):
        store = PaperPortfolioStore("unused-paper.json")
        state_data = {"version": 1, "users": {}}
        with patch.object(store, "_load_unlocked", return_value=state_data), patch(
            "paper_portfolio._atomic_write_json"
        ):
            store.set_capital("user", 100_000)
            store.buy("user", "600001", 100, 10, "稳健股份")
            state = store.buy("user", "600001", 100, 12, "稳健股份")
            self.assertEqual(state["positions"]["600001"]["quantity"], 200)
            self.assertAlmostEqual(state["positions"]["600001"]["avg_cost"], 11)
            state = store.sell("user", "600001", 100, 13)
            self.assertAlmostEqual(state["realized_pnl"], 200)
            self.assertEqual(state["positions"]["600001"]["quantity"], 100)

    def test_cross_sectional_factors_and_paper_reports(self):
        service = StockResearchService(
            provider=FactorProvider(),
            minimum_market_size=1,
            shortlist_size=3,
        )
        analyses = service.screen_market(date(2026, 7, 10))
        self.assertEqual(analyses[0].code, "600001")
        self.assertIn("趋势", analyses[0].factor_scores)
        self.assertGreater(analyses[0].score, analyses[-1].score)
        self.assertLess(analyses[0].stop_loss, analyses[0].price)

        empty = {
            "capital": 100_000,
            "capital_is_default": False,
            "cash": 100_000,
            "positions": {},
        }
        picks = service.paper_pick_report(empty, date(2026, 7, 10))
        self.assertIn("/paper buy 600001", picks)

        held = {
            **empty,
            "cash": 97_500,
            "positions": {
                "600001": {"code": "600001", "name": "稳健股份", "quantity": 100, "avg_cost": 25},
            },
        }
        report = service.paper_portfolio_report(held, date(2026, 7, 10))
        self.assertIn("稳健股份", report)
        self.assertIn("动作：", report)


if __name__ == "__main__":
    unittest.main()
