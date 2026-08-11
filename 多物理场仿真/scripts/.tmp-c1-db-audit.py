from __future__ import annotations

import json
import sqlite3
import sys
import hashlib
from pathlib import Path


path = Path(sys.argv[1]).resolve()
benchmark_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
try:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    result = {}
    for table in tables:
        columns = [
            {"name": row[1], "type": row[2]}
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        ]
        count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        names = {item["name"] for item in columns}
        date_column = next(
            (name for name in ("date", "trade_date", "snapshot_date", "effective_date") if name in names),
            None,
        )
        date_range = None
        if date_column:
            date_range = connection.execute(
                f'SELECT MIN("{date_column}"), MAX("{date_column}") FROM "{table}"'
            ).fetchone()
        result[table] = {
            "columns": columns,
            "count": count,
            "date_column": date_column,
            "date_range": date_range,
        }
    research = {}
    research["membership_snapshots"] = [dict(zip(("signal_date", "source_date", "count"), row)) for row in connection.execute(
        "SELECT signal_date, MIN(source_date), COUNT(*) FROM memberships WHERE signal_date BETWEEN '2019-01-01' AND '2024-12-31' GROUP BY signal_date ORDER BY signal_date"
    )]
    research["membership_bad_source_dates"] = connection.execute(
        "SELECT COUNT(*) FROM memberships WHERE signal_date BETWEEN '2019-01-01' AND '2024-12-31' AND source_date > signal_date"
    ).fetchone()[0]
    research["industry_bad_source_dates"] = connection.execute(
        "SELECT COUNT(*) FROM industries WHERE signal_date BETWEEN '2019-01-01' AND '2024-12-31' AND source_date > signal_date"
    ).fetchone()[0]
    research["industry_snapshots"] = [dict(zip(("signal_date", "source_date", "count", "classified"), row)) for row in connection.execute(
        "SELECT signal_date, MIN(source_date), COUNT(*), SUM(CASE WHEN industry IS NOT NULL AND TRIM(industry) <> '' THEN 1 ELSE 0 END) FROM industries WHERE signal_date BETWEEN '2019-01-01' AND '2024-12-31' GROUP BY signal_date ORDER BY signal_date"
    )]
    columns = ("rows", "codes", "first_date", "last_date", "null_close", "null_open", "null_amount", "null_turn", "null_pe", "null_pb", "null_status", "null_st")
    row = connection.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT p.code), MIN(p.date), MAX(p.date),
          SUM(p.close IS NULL OR p.close <= 0), SUM(p.open IS NULL OR p.open <= 0),
          SUM(p.amount IS NULL), SUM(p.turn IS NULL), SUM(p.pe_ttm IS NULL),
          SUM(p.pb_mrq IS NULL), SUM(p.tradestatus IS NULL), SUM(p.is_st IS NULL)
        FROM prices p
        WHERE p.date BETWEEN '2019-01-01' AND '2024-12-31'
          AND EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.code=p.code
              AND m.signal_date=(SELECT MAX(m2.signal_date) FROM memberships m2 WHERE m2.signal_date <= p.date)
          )
        """
    ).fetchone()
    research["member_price_coverage"] = dict(zip(columns, row))
    research["price_rows_after_research_end"] = connection.execute(
        "SELECT COUNT(*) FROM prices WHERE date > '2024-12-31'"
    ).fetchone()[0]
    research["research_trading_days"] = connection.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM trading_days WHERE date BETWEEN '2019-01-01' AND '2024-12-31'"
    ).fetchone()
    research["daily_field_examples"] = [dict(zip(("date", "code", "close", "turn", "pe_ttm", "pb_mrq", "tradestatus", "is_st"), row)) for row in connection.execute(
        "SELECT date,code,close,turn,pe_ttm,pb_mrq,tradestatus,is_st FROM prices WHERE date BETWEEN '2019-01-01' AND '2024-12-31' ORDER BY date,code LIMIT 5"
    )]
    result["research_audit"] = research
    if benchmark_path:
        raw = benchmark_path.read_bytes()
        lines = benchmark_path.read_text(encoding="utf-8-sig").splitlines()
        data_lines = [line for line in lines if line and not line.startswith("#")]
        result["benchmark"] = {
            "path": str(benchmark_path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "header": data_lines[0] if data_lines else None,
            "first": data_lines[1] if len(data_lines) > 1 else None,
            "last": data_lines[-1] if len(data_lines) > 1 else None,
            "rows": max(0, len(data_lines) - 1),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
finally:
    connection.close()
