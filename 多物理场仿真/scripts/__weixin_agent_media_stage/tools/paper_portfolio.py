"""Persistent paper-trading ledger for the Weixin stock research tool."""

from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_CAPITAL = 100_000.0


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)


class PaperPortfolioStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _load_unlocked(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"version": 1, "users": {}}
        if not isinstance(raw, dict) or not isinstance(raw.get("users"), dict):
            return {"version": 1, "users": {}}
        return raw

    @staticmethod
    def _new_user() -> dict[str, Any]:
        return {
            "capital": DEFAULT_CAPITAL,
            "capital_is_default": True,
            "cash": DEFAULT_CAPITAL,
            "realized_pnl": 0.0,
            "positions": {},
            "trades": [],
        }

    def _user_unlocked(self, data: dict[str, Any], user_id: str) -> dict[str, Any]:
        users = data.setdefault("users", {})
        user = users.setdefault(user_id, self._new_user())
        user.setdefault("capital", DEFAULT_CAPITAL)
        user.setdefault("capital_is_default", True)
        user.setdefault("cash", float(user["capital"]))
        user.setdefault("realized_pnl", 0.0)
        user.setdefault("positions", {})
        user.setdefault("trades", [])
        return user

    def snapshot(self, user_id: str) -> dict[str, Any]:
        with self.lock:
            data = self._load_unlocked()
            user = self._user_unlocked(data, user_id)
            return copy.deepcopy(user)

    def set_capital(self, user_id: str, capital: float) -> dict[str, Any]:
        capital = float(capital)
        if not 1_000 <= capital <= 100_000_000:
            raise ValueError("模拟本金需在 1000 到 1 亿元之间")
        with self.lock:
            data = self._load_unlocked()
            user = self._user_unlocked(data, user_id)
            book_cost = sum(
                float(item.get("quantity", 0)) * float(item.get("avg_cost", 0))
                for item in user["positions"].values()
            )
            user["capital"] = capital
            user["capital_is_default"] = False
            user["cash"] = capital - book_cost + float(user.get("realized_pnl", 0))
            _atomic_write_json(self.path, data)
            return copy.deepcopy(user)

    def import_snapshot(
        self,
        user_id: str,
        capital: float,
        cash: float,
        positions: list[dict[str, Any]],
        source: str = "manual snapshot",
    ) -> dict[str, Any]:
        capital = float(capital)
        cash = float(cash)
        if not 1_000 <= capital <= 100_000_000:
            raise ValueError("模拟本金需在 1000 到 1 亿元之间")
        if cash < 0 or cash > capital * 2:
            raise ValueError("模拟现金无效")
        imported: dict[str, dict[str, Any]] = {}
        now = datetime.now().isoformat(timespec="seconds")
        for raw in positions:
            code = str(raw.get("code") or "").strip()
            name = str(raw.get("name") or "").strip()
            quantity = int(raw.get("quantity") or 0)
            avg_cost = float(raw.get("avg_cost") or 0)
            if len(code) != 6 or not code.isdigit():
                raise ValueError(f"持仓代码无效: {code}")
            if quantity <= 0 or avg_cost <= 0:
                raise ValueError(f"持仓数量或成本无效: {code}")
            imported[code] = {
                "code": code,
                "name": name,
                "quantity": quantity,
                "avg_cost": round(avg_cost, 4),
                "opened_at": now,
                "updated_at": now,
                "source": source,
            }
        if not imported:
            raise ValueError("没有可导入的持仓")
        with self.lock:
            data = self._load_unlocked()
            user = self._user_unlocked(data, user_id)
            user["capital"] = capital
            user["capital_is_default"] = False
            user["cash"] = cash
            user["realized_pnl"] = 0.0
            user["positions"] = imported
            user["snapshot_imported_at"] = now
            user["snapshot_source"] = source
            user["trades"].append({
                "side": "snapshot_import",
                "positions": len(imported),
                "capital": capital,
                "cash": cash,
                "source": source,
                "time": now,
            })
            _atomic_write_json(self.path, data)
            return copy.deepcopy(user)

    def buy(
        self,
        user_id: str,
        code: str,
        quantity: int,
        price: float,
        name: str = "",
    ) -> dict[str, Any]:
        quantity = int(quantity)
        price = float(price)
        if quantity <= 0 or quantity > 100_000_000:
            raise ValueError("买入股数必须是正整数")
        if price <= 0 or price > 100_000:
            raise ValueError("买入价格无效")
        with self.lock:
            data = self._load_unlocked()
            user = self._user_unlocked(data, user_id)
            positions = user["positions"]
            current = positions.get(code, {})
            old_quantity = int(current.get("quantity", 0))
            old_cost = float(current.get("avg_cost", 0))
            new_quantity = old_quantity + quantity
            avg_cost = (old_quantity * old_cost + quantity * price) / new_quantity
            positions[code] = {
                "code": code,
                "name": name or current.get("name", ""),
                "quantity": new_quantity,
                "avg_cost": round(avg_cost, 4),
                "opened_at": current.get("opened_at") or datetime.now().isoformat(timespec="seconds"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            user["cash"] = float(user.get("cash", 0)) - quantity * price
            user["trades"].append({
                "side": "buy",
                "code": code,
                "name": name,
                "quantity": quantity,
                "price": price,
                "time": datetime.now().isoformat(timespec="seconds"),
            })
            _atomic_write_json(self.path, data)
            return copy.deepcopy(user)

    def sell(
        self,
        user_id: str,
        code: str,
        quantity: int,
        price: float,
    ) -> dict[str, Any]:
        quantity = int(quantity)
        price = float(price)
        if quantity <= 0 or price <= 0:
            raise ValueError("卖出股数和价格必须大于 0")
        with self.lock:
            data = self._load_unlocked()
            user = self._user_unlocked(data, user_id)
            current = user["positions"].get(code)
            if not current:
                raise ValueError(f"模拟持仓里没有 {code}")
            held = int(current.get("quantity", 0))
            if quantity > held:
                raise ValueError(f"卖出股数超过持仓，当前只有 {held} 股")
            avg_cost = float(current.get("avg_cost", 0))
            realized = (price - avg_cost) * quantity
            remaining = held - quantity
            if remaining:
                current["quantity"] = remaining
                current["updated_at"] = datetime.now().isoformat(timespec="seconds")
            else:
                user["positions"].pop(code, None)
            user["cash"] = float(user.get("cash", 0)) + quantity * price
            user["realized_pnl"] = float(user.get("realized_pnl", 0)) + realized
            user["trades"].append({
                "side": "sell",
                "code": code,
                "name": current.get("name", ""),
                "quantity": quantity,
                "price": price,
                "realized_pnl": round(realized, 2),
                "time": datetime.now().isoformat(timespec="seconds"),
            })
            _atomic_write_json(self.path, data)
            return copy.deepcopy(user)

    @staticmethod
    def _match_position(user: dict[str, Any], query: str) -> tuple[str, dict[str, Any]]:
        query = str(query or "").strip()
        matches = [
            (code, position)
            for code, position in user["positions"].items()
            if code in query or (position.get("name") and position["name"] in query)
        ]
        if not matches and len(user["positions"]) == 1:
            matches = list(user["positions"].items())
        if not matches:
            raise ValueError("没看出你卖完的是哪一只，请带上股票名称或六位代码")
        if len(matches) > 1:
            raise ValueError("这句话匹配到多只持仓，请单独告诉我卖完了哪一只")
        return matches[0]

    def close_position(
        self,
        user_id: str,
        query: str,
        price: float | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        actual_price = float(price) if price is not None else None
        if actual_price is not None and (actual_price <= 0 or actual_price > 100_000):
            raise ValueError("卖出价格无效")
        with self.lock:
            data = self._load_unlocked()
            user = self._user_unlocked(data, user_id)
            code, position = self._match_position(user, query)
            quantity = int(position.get("quantity", 0))
            avg_cost = float(position.get("avg_cost", 0))
            estimated = actual_price is None
            booked_price = avg_cost if estimated else actual_price
            realized = 0.0 if estimated else (booked_price - avg_cost) * quantity
            user["positions"].pop(code, None)
            user["cash"] = float(user.get("cash", 0)) + quantity * booked_price
            user["realized_pnl"] = float(user.get("realized_pnl", 0)) + realized
            trade = {
                "side": "sell",
                "code": code,
                "name": position.get("name", ""),
                "quantity": quantity,
                "price": round(booked_price, 4),
                "avg_cost": round(avg_cost, 4),
                "realized_pnl": round(realized, 2),
                "estimated_price": estimated,
                "time": datetime.now().isoformat(timespec="seconds"),
            }
            user["trades"].append(trade)
            _atomic_write_json(self.path, data)
            return copy.deepcopy(user), copy.deepcopy(trade)

    def settle_estimated_close(
        self,
        user_id: str,
        query: str,
        price: float,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        price = float(price)
        if price <= 0 or price > 100_000:
            raise ValueError("实际成交价无效")
        with self.lock:
            data = self._load_unlocked()
            user = self._user_unlocked(data, user_id)
            candidates = [
                trade for trade in reversed(user["trades"])
                if trade.get("side") == "sell"
                and trade.get("estimated_price")
                and (
                    str(trade.get("code") or "") in query
                    or (trade.get("name") and str(trade["name"]) in query)
                )
            ]
            if not candidates:
                candidates = [
                    trade for trade in reversed(user["trades"])
                    if trade.get("side") == "sell" and trade.get("estimated_price")
                ]
            if not candidates:
                raise ValueError("没有等待补成交价的清仓记录")
            trade = candidates[0]
            quantity = int(trade.get("quantity", 0))
            old_price = float(trade.get("price", 0))
            avg_cost = float(trade.get("avg_cost", old_price))
            cash_delta = (price - old_price) * quantity
            realized = (price - avg_cost) * quantity
            user["cash"] = float(user.get("cash", 0)) + cash_delta
            user["realized_pnl"] = float(user.get("realized_pnl", 0)) + realized
            trade["price"] = round(price, 4)
            trade["realized_pnl"] = round(realized, 2)
            trade["estimated_price"] = False
            trade["settled_at"] = datetime.now().isoformat(timespec="seconds")
            _atomic_write_json(self.path, data)
            return copy.deepcopy(user), copy.deepcopy(trade)
