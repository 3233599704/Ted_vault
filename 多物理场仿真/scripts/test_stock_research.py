import unittest
from copy import deepcopy
from datetime import date, datetime, timedelta
from unittest.mock import patch

from stock_research import (
    StockResearchService,
    WatchlistStore,
    extract_stock_codes,
    is_report_due,
    normalize_stock_code,
)


def make_history(
    start_price: float,
    daily_step: float,
    days: int = 90,
    latest_volume_multiplier: float = 1.4,
) -> list[dict]:
    start = date(2026, 1, 2)
    rows = []
    for index in range(days):
        rows.append(
            {
                "日期": (start + timedelta(days=index)).isoformat(),
                "收盘": start_price + daily_step * index,
                "成交量": 1_000_000,
            }
        )
    rows[-1]["成交量"] *= latest_volume_multiplier
    return rows


class FakeProvider:
    name = "固定测试数据"

    def __init__(self):
        self.snapshots = [
            {
                "代码": "600001",
                "名称": "稳健股份",
                "最新价": 18.9,
                "涨跌幅": 1.2,
                "成交额": 800_000_000,
                "换手率": 2.1,
                "量比": 1.4,
                "市盈率-动态": 22,
                "市净率": 2.4,
            },
            {
                "代码": "000002",
                "名称": "震荡股份",
                "最新价": 10.0,
                "涨跌幅": 0.2,
                "成交额": 500_000_000,
                "换手率": 1.8,
                "量比": 1.1,
                "市盈率-动态": 30,
                "市净率": 2.0,
            },
            {
                "代码": "300003",
                "名称": "ST风险股",
                "最新价": 6.0,
                "涨跌幅": 1.0,
                "成交额": 300_000_000,
                "换手率": 2.0,
                "量比": 1.2,
                "市盈率-动态": 20,
                "市净率": 2.0,
            },
        ]
        self.histories = {
            "600001": make_history(10, 0.1),
            "000002": make_history(10, 0),
        }

    def market_snapshot(self):
        return self.snapshots

    def history(self, code, start, end):
        if code == "000002":
            raise RuntimeError("模拟单只股票接口失败")
        return self.histories[code]

    def latest_financials(self, today):
        return {
            "600001": {
                "营业总收入同比增长": 12.0,
                "净利润同比增长": 15.0,
                "净资产收益率": 11.0,
            }
        }

    def recent_notices(self, today, days=7):
        return {"600001": ["关于股份回购进展的公告"]}

    def trading_days(self):
        return {date(2026, 6, 15)}


class StockCodeTests(unittest.TestCase):
    def test_normalizes_exchange_suffixes(self):
        self.assertEqual(normalize_stock_code("600519.SH"), "600519")
        self.assertEqual(normalize_stock_code("sz000001"), "000001")
        self.assertEqual(normalize_stock_code("BJ920001"), "920001")
        self.assertIsNone(normalize_stock_code("123456"))

    def test_natural_language_requires_context(self):
        self.assertEqual(extract_stock_codes("验证码是 600519"), [])
        self.assertEqual(extract_stock_codes("帮我看看 600519 的走势"), ["600519"])
        self.assertEqual(
            extract_stock_codes("/stock 600519 000001", require_context=False),
            ["600519", "000001"],
        )


class WatchlistTests(unittest.TestCase):
    def test_add_remove_duplicate_and_user_isolation(self):
        memory = {}
        store = WatchlistStore("unused-test-path.json")
        store._load_unlocked = lambda: deepcopy(memory)

        def save_to_memory(path, payload):
            memory.clear()
            memory.update(deepcopy(payload))

        with patch("stock_research._atomic_write_json", side_effect=save_to_memory):
            self.assertTrue(store.add("user-a", "600519"))
            self.assertFalse(store.add("user-a", "600519"))
            self.assertEqual(store.list("user-a"), ["600519"])
            self.assertEqual(store.list("user-b"), [])
            self.assertTrue(store.remove("user-a", "600519"))
            self.assertFalse(store.remove("user-a", "600519"))


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.service = StockResearchService(
            provider=FakeProvider(),
            cache_seconds=60,
            shortlist_size=10,
        )

    def test_market_screen_filters_risk_and_survives_one_failure(self):
        results = self.service.screen_market(date(2026, 6, 15))
        self.assertEqual([item.code for item in results], ["600001"])
        self.assertGreaterEqual(results[0].score, 65)
        self.assertEqual(results[0].trend, "稳步向上")

    def test_report_is_plain_language_and_not_an_order(self):
        report = self.service.market_report(date(2026, 6, 15))
        self.assertIn("模拟关注名单", report)
        self.assertIn("为什么值得看", report)
        self.assertIn("需要小心", report)
        self.assertIn("不构成真实交易建议", report)
        for prohibited in ("建议买入", "建议卖出", "目标价", "保证盈利", "收益承诺"):
            self.assertNotIn(prohibited, report)

    def test_unknown_code_is_reported_without_invented_data(self):
        report = self.service.code_report(["688999"], date(2026, 6, 15))
        self.assertIn("没有在当前 A 股代码表中找到", report)
        self.assertNotIn("收盘价", report)

    def test_trading_calendar(self):
        self.assertTrue(self.service.is_trading_day(date(2026, 6, 15)))
        self.assertFalse(self.service.is_trading_day(date(2026, 6, 14)))


class SchedulerTests(unittest.TestCase):
    def test_report_due_once_after_close(self):
        now = datetime(2026, 6, 15, 15, 30)
        self.assertTrue(is_report_due(now, 15, 30, None, True))
        self.assertFalse(is_report_due(now, 15, 30, "2026-06-15", True))
        self.assertFalse(
            is_report_due(datetime(2026, 6, 15, 15, 29), 15, 30, None, True)
        )
        self.assertFalse(is_report_due(now, 15, 30, None, False))


if __name__ == "__main__":
    unittest.main()
