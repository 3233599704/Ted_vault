import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from stock_research import EastmoneyPublicProvider, StockResearchService


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


if __name__ == "__main__":
    unittest.main()
