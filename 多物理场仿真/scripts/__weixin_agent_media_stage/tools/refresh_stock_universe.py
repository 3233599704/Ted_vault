"""Refresh the reproducible CSI 300 stock universe used by the Weixin agent."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "csi300-universe.json"


def normalize_date(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value or "")[:10]


def load_industries(as_of: str) -> dict[str, str]:
    try:
        import baostock as bs
    except ImportError as exc:
        raise SystemExit("缺少 BaoStock，请先运行：npm run setup:stocks") from exc
    login = bs.login()
    if login.error_code != "0":
        raise SystemExit(f"BaoStock 登录失败：{login.error_msg}")
    try:
        result = bs.query_stock_industry(date=as_of)
        industries: dict[str, str] = {}
        while result.error_code == "0" and result.next():
            row = result.get_row_data()
            if len(row) < 4:
                continue
            code = str(row[1]).split(".")[-1].zfill(6)
            industry = str(row[3] or "").strip()
            if len(code) == 6 and industry:
                industries[code] = industry
        if result.error_code != "0":
            raise SystemExit(f"BaoStock 行业分类失败：{result.error_msg}")
        return industries
    finally:
        bs.logout()


def write_payload(payload: dict[str, Any]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp, OUTPUT)


def enrich_industries_only() -> int:
    try:
        payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise SystemExit("现有沪深300股票池不可用，请先运行完整更新") from exc
    industries = load_industries(str(payload.get("as_of") or ""))
    matched = 0
    for item in payload.get("stocks", []):
        industry = industries.get(str(item.get("code") or "").zfill(6))
        if industry:
            item["industry"] = industry
            matched += 1
    if matched < 250:
        raise SystemExit(f"行业分类只匹配 {matched} 只，拒绝覆盖现有股票池")
    payload["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    payload["source"] = "AKShare/CSI 成分 + BaoStock 点时行业分类"
    write_payload(payload)
    print(f"已补充 {matched} 只沪深300行业分类：{OUTPUT}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--industry-only", action="store_true")
    args = parser.parse_args()
    if args.industry_only:
        return enrich_industries_only()

    try:
        import akshare as ak
    except ImportError as exc:
        raise SystemExit("缺少 AKShare，请先运行：npm run setup:stocks") from exc

    frame = ak.index_stock_cons_csindex(symbol="000300")
    if frame is None or len(frame) < 250:
        count = 0 if frame is None else len(frame)
        raise SystemExit(f"沪深300成分股只取得 {count} 条，拒绝覆盖现有股票池")

    dates = [normalize_date(value) for value in frame["日期"].tolist()]
    as_of = max(value for value in dates if value)
    industries = load_industries(as_of)
    stocks = []
    for row in frame.to_dict("records"):
        code = str(row.get("成分券代码") or "").zfill(6)
        name = str(row.get("成分券名称") or "").strip()
        if len(code) == 6 and code.isdigit() and name:
            stocks.append({"code": code, "name": name, "industry": industries.get(code, "")})
    stocks = list({item["code"]: item for item in stocks}.values())
    if len(stocks) < 250:
        raise SystemExit(f"清洗后只剩 {len(stocks)} 条成分股，拒绝覆盖现有股票池")

    matched = sum(bool(item.get("industry")) for item in stocks)
    if matched < 250:
        raise SystemExit(f"行业分类只匹配 {matched} 只，拒绝覆盖现有股票池")
    payload = {
        "index_code": "000300",
        "index_name": "沪深300",
        "as_of": as_of,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "AKShare/CSI 成分 + BaoStock 点时行业分类",
        "stocks": stocks,
    }
    write_payload(payload)
    print(f"已更新 {OUTPUT}：{len(stocks)} 只，成分日期 {payload['as_of']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
