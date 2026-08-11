"""JSON command bridge between the Node agent and stock_research.py."""

from __future__ import annotations

import json
import sys
from datetime import date

from stock_research import StockResearchService, WatchlistStore


def respond(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    request = json.load(sys.stdin)
    action = str(request.get("action") or "")
    args = request.get("args") or {}
    user_id = str(request.get("user_id") or "")
    store = WatchlistStore(request["watchlist_path"])

    if action == "watch_add":
        added = [code for code in args.get("codes", []) if store.add(user_id, code)]
        text = "已加入关注：" + "、".join(added) if added else "这些股票已经在关注列表里了。"
        return respond({"ok": True, "text": text, "provider": "local"})
    if action == "watch_remove":
        removed = [code for code in args.get("codes", []) if store.remove(user_id, code)]
        text = "已移出关注：" + "、".join(removed) if removed else "关注列表里没有这些股票。"
        return respond({"ok": True, "text": text, "provider": "local"})
    if action == "watch_list":
        codes = store.list(user_id)
        text = "当前关注：" + "、".join(codes) if codes else "股票关注列表还是空的。"
        return respond({"ok": True, "text": text, "provider": "local"})

    service = StockResearchService()
    if action == "scheduled_daily" and not service.is_trading_day(date.today()):
        return respond({"ok": True, "text": "", "skipped": True, "provider": service.provider.name})
    if action in {"watch_report", "scheduled_daily"}:
        codes = store.list(user_id)
        text = service.code_report(codes) if codes else service.market_report()
    elif action == "code_report":
        text = service.code_report(list(args.get("codes") or []))
    elif action == "report":
        text = service.market_report()
    else:
        raise ValueError(f"未知股票动作: {action}")
    respond({"ok": True, "text": text, "provider": service.provider.name})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        respond({"ok": False, "error": str(exc)[:1000]})
