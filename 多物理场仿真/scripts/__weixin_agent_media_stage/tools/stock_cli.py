"""JSON command bridge between the Node agent and stock_research.py."""

from __future__ import annotations

import json
import sys
from datetime import date
from datetime import datetime
from pathlib import Path

from paper_portfolio import PaperPortfolioStore
from stock_research import StockResearchService, WatchlistStore, normalize_stock_code


def respond(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


def append_journal(path: str, job_id: str, title: str, text: str) -> None:
    if not path:
        return
    target = Path(path)
    marker = f"<!-- vera-stock-job:{job_id} -->"
    try:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if marker in existing:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        header = "" if existing.strip() else "# 同花顺模拟盘复盘\n\n"
        entry = (
            f"{header}\n{marker}\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}｜{title}\n\n"
            f"{text.strip()}\n\n---\n"
        )
        with target.open("a", encoding="utf-8") as handle:
            handle.write(entry)
    except OSError:
        pass


def main() -> None:
    request = json.load(sys.stdin)
    action = str(request.get("action") or "")
    args = request.get("args") or {}
    user_id = str(request.get("user_id") or "")
    job_id = str(request.get("job_id") or f"manual-{datetime.now().timestamp()}")
    journal_path = str(request.get("journal_path") or "")
    store = WatchlistStore(request["watchlist_path"])
    portfolio = PaperPortfolioStore(request["portfolio_path"])

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

    if action == "theme_watch_add":
        themes = list(dict.fromkeys(
            str(item).strip() for item in args.get("themes", []) if str(item).strip()
        ))
        if not themes:
            return respond({
                "ok": True,
                "text": "我还没识别出具体研究方向，你可以直接说能源、AI 上游或机器人这类板块。",
                "provider": "local",
            })
        service = StockResearchService()
        try:
            grouped = service.theme_candidates(themes)
        except Exception as exc:
            return respond({
                "ok": True,
                "text": (
                    f"已记下持续关注方向：{'、'.join(themes)}。"
                    f"\n这次实时行情连接失败，暂时没乱选候选；之后的每日推送会继续重试。"
                    f"\n接口信息：{str(exc)[:160]}"
                ),
                "provider": service.provider.name,
            })
        lines = [f"已开始持续关注：{'、'.join(themes)}。"]
        added_any = False
        for theme in themes:
            candidates = grouped.get(theme, [])
            if not candidates:
                lines.append(f"{theme}：当前没有通过流动性、估值和异常波动过滤的候选。")
                continue
            labels = []
            for item in candidates:
                store.add(user_id, item["code"])
                labels.append(f"{item['name']}（{item['code']}）")
                added_any = True
            lines.append(f"{theme}观察池：{'、'.join(labels)}")
        if added_any:
            lines.append("这些只是实时行情筛出的观察候选，不等于建议现在买入。")
        return respond({"ok": True, "text": "\n".join(lines), "provider": service.provider.name})

    if action == "paper_set_capital":
        state = portfolio.set_capital(user_id, float(args.get("capital") or 0))
        text = (
            f"模拟盘本金已设为 {state['capital']:,.0f} 元。\n"
            f"当前估算现金：{state['cash']:,.0f} 元。以后用 /stock picks 生成分批计划。"
        )
        append_journal(journal_path, job_id, "设置模拟本金", text)
        return respond({"ok": True, "text": text, "provider": "local"})

    if action == "paper_import_snapshot":
        state = portfolio.import_snapshot(
            user_id,
            float(args.get("capital") or 0),
            float(args.get("cash") or 0),
            list(args.get("positions") or []),
            str(args.get("source") or "持仓截图"),
        )
        lines = [
            f"已导入模拟盘持仓：{len(state['positions'])} 只，账户基准 {state['capital']:,.2f} 元，可用现金 {state['cash']:,.2f} 元。",
        ]
        for position in state["positions"].values():
            lines.append(
                f"{position.get('name') or position['code']}（{position['code']}）："
                f"{position['quantity']} 股，成本 {position['avg_cost']:.4f} 元"
            )
        text = "\n".join(lines)
        append_journal(journal_path, job_id, "导入持仓快照", text)
        return respond({"ok": True, "text": text, "provider": "local paper ledger"})

    if action in {"paper_buy", "paper_sell"}:
        code = normalize_stock_code(str(args.get("code") or ""))
        if not code:
            raise ValueError("不是支持的 A 股代码")
        quantity = int(args.get("quantity") or 0)
        price = float(args.get("price") or 0)
        service = StockResearchService()
        if action == "paper_buy":
            try:
                identity = service.stock_identity(code)
            except Exception:
                identity = None
            name = identity[1] if identity else ""
            state = portfolio.buy(user_id, code, quantity, price, name)
            position = state["positions"][code]
            text = (
                f"已记录模拟买入：{name or code}（{code}）{quantity} 股，成交价 {price:.2f}。\n"
                f"当前持仓：{position['quantity']} 股，平均成本 {position['avg_cost']:.2f}；"
                f"估算现金 {state['cash']:,.0f} 元。\n"
                "我会在每日持仓检查中跟踪硬止损、趋势和仓位集中度。"
            )
            title = f"模拟买入 {code}"
        else:
            state = portfolio.sell(user_id, code, quantity, price)
            remaining = state["positions"].get(code)
            remain_text = f"剩余 {remaining['quantity']} 股" if remaining else "该股已清仓"
            text = (
                f"已记录模拟卖出：{code} {quantity} 股，成交价 {price:.2f}，{remain_text}。\n"
                f"累计已实现盈亏：{state['realized_pnl']:+,.0f} 元；估算现金 {state['cash']:,.0f} 元。"
            )
            title = f"模拟卖出 {code}"
        append_journal(journal_path, job_id, title, text)
        return respond({"ok": True, "text": text, "provider": "local paper ledger"})

    if action == "paper_close":
        try:
            raw_price = args.get("price")
            price = float(raw_price) if raw_price is not None else None
            state, trade = portfolio.close_position(
                user_id,
                str(args.get("query") or ""),
                price,
            )
        except ValueError as exc:
            return respond({"ok": True, "text": str(exc), "provider": "local paper ledger"})
        name = trade.get("name") or trade["code"]
        if trade.get("estimated_price"):
            text = (
                f"已经把 {name}（{trade['code']}）从当前持仓中移除；"
                f"以后每日推送不会再分析它。\n"
                f"你还没给实际成交价，现金暂按成本 {trade['price']:.4f} 元估算。"
                f"再告诉我“{name}实际成交价 xx 元”，我会自动校正现金和已实现盈亏。"
            )
        else:
            text = (
                f"已记录清仓：{name}（{trade['code']}）{trade['quantity']} 股，"
                f"成交价 {trade['price']:.4f} 元。\n"
                f"已实现盈亏 {trade['realized_pnl']:+,.2f} 元；当前现金 {state['cash']:,.2f} 元。"
                f"以后每日推送只分析剩余 {len(state['positions'])} 只持仓。"
            )
        append_journal(journal_path, job_id, f"清仓 {trade['code']}", text)
        return respond({"ok": True, "text": text, "provider": "local paper ledger"})

    if action == "paper_settle_close":
        try:
            state, trade = portfolio.settle_estimated_close(
                user_id,
                str(args.get("query") or ""),
                float(args.get("price") or 0),
            )
        except ValueError as exc:
            return respond({"ok": True, "text": str(exc), "provider": "local paper ledger"})
        name = trade.get("name") or trade["code"]
        text = (
            f"已把 {name}（{trade['code']}）的清仓价校正为 {trade['price']:.4f} 元。\n"
            f"该笔已实现盈亏 {trade['realized_pnl']:+,.2f} 元；当前现金 {state['cash']:,.2f} 元。"
        )
        append_journal(journal_path, job_id, f"补录清仓价 {trade['code']}", text)
        return respond({"ok": True, "text": text, "provider": "local paper ledger"})

    service = StockResearchService()
    if action == "scheduled_daily" and not service.is_trading_day(date.today()):
        return respond({"ok": True, "text": "", "skipped": True, "provider": service.provider.name})
    if action == "paper_portfolio":
        text = service.paper_portfolio_report(portfolio.snapshot(user_id))
    elif action == "picks":
        text = service.paper_pick_report(portfolio.snapshot(user_id))
    elif action == "scheduled_daily":
        state = portfolio.snapshot(user_id)
        sections = []
        if args.get("includeHoldings", True):
            sections.append(service.paper_portfolio_report(state))
        if args.get("includePicks", True):
            sections.append("===== 新候选 =====\n\n" + service.paper_pick_report(state))
        if args.get("includeWatchlist", False):
            codes = store.list(user_id)
            if codes:
                themes = [str(item) for item in args.get("researchThemes", []) if str(item).strip()]
                theme_line = f"关注方向：{'、'.join(themes)}\n\n" if themes else ""
                sections.append(
                    "===== 持续关注 =====\n\n"
                    + theme_line
                    + service.watchlist_report(codes)
                )
        text = "\n\n".join(sections)
    elif action == "watch_report":
        codes = store.list(user_id)
        text = service.code_report(codes) if codes else service.market_report()
    elif action == "code_report":
        text = service.code_report(list(args.get("codes") or []))
    elif action == "report":
        text = service.market_report()
    else:
        raise ValueError(f"未知股票动作: {action}")
    if action in {"paper_portfolio", "picks", "scheduled_daily"}:
        titles = {
            "paper_portfolio": "模拟持仓检查",
            "picks": "模拟盘候选",
            "scheduled_daily": "每日模拟盘报告",
        }
        append_journal(journal_path, job_id, titles[action], text)
    respond({"ok": True, "text": text, "provider": service.provider.name})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        respond({"ok": False, "error": str(exc)[:1000]})
