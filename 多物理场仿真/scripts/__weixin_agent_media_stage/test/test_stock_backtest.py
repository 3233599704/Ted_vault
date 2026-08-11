import tempfile
import unittest
from pathlib import Path

import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from stock_backtest import PointInTimeBacktester, performance_metrics, split_metrics


class BacktestMathTests(unittest.TestCase):
    def test_feature_history_never_reads_after_signal_date(self):
        frame = pd.DataFrame(
            {
                "close": [10.0, 11.0, 999.0],
            },
            index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        )
        history = PointInTimeBacktester._history_asof(
            frame,
            pd.Timestamp("2024-01-03"),
        )
        self.assertEqual(list(history["close"]), [10.0, 11.0])

    def test_performance_metrics_include_drawdown_and_compounding(self):
        returns = pd.Series(
            [0.10, -0.20, 0.05],
            index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        )
        metrics = performance_metrics(returns)
        self.assertAlmostEqual(metrics["total_return"], 1.1 * 0.8 * 1.05 - 1)
        self.assertAlmostEqual(metrics["max_drawdown"], -0.2)

    def test_metrics_use_trading_months_and_include_starting_nav_drawdown(self):
        returns = pd.Series(
            [-0.10, 0.05],
            index=pd.to_datetime(["2024-01-31", "2024-02-29"]),
        )
        metrics = performance_metrics(returns)
        total = 0.9 * 1.05 - 1
        self.assertEqual(metrics["trading_months"], 2)
        self.assertAlmostEqual(metrics["total_return"], total)
        self.assertAlmostEqual(metrics["annual_return"], (1 + total) ** 6 - 1)
        self.assertAlmostEqual(metrics["max_drawdown"], -0.10)

    def test_split_metrics_keep_period_costs_and_turnover_separate(self):
        index = pd.to_datetime([
            "2024-01-31", "2024-02-29", "2024-03-29", "2024-04-30",
        ])
        result = pd.DataFrame({
            "return": [0.01, 0.02, -0.01, 0.03],
            "equity": [101.0, 103.02, 101.9898, 105.049494],
            "turnover_notional": [100.0, 50.0, 20.0, 10.0],
            "transaction_cost": [1.0, 2.0, 3.0, 4.0],
        }, index=index)
        benchmark = pd.Series([0.0, 0.01, -0.02, 0.01], index=index)
        metrics = split_metrics(result, benchmark, {
            "first": ("2024-01-01", "2024-02-29"),
            "second": ("2024-03-01", "2024-04-30"),
        })
        self.assertEqual(metrics["first"]["period"]["trading_months"], 2)
        self.assertEqual(metrics["second"]["period"]["trading_months"], 2)
        self.assertAlmostEqual(
            metrics["first"]["strategy"]["transaction_cost_amount"], 3.0,
        )
        self.assertAlmostEqual(
            metrics["second"]["strategy"]["transaction_cost_amount"], 7.0,
        )
        self.assertGreater(
            metrics["first"]["strategy"]["annual_turnover"],
            metrics["second"]["strategy"]["annual_turnover"],
        )


if __name__ == "__main__":
    unittest.main()
