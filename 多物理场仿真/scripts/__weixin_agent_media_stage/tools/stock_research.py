"""A-share research helpers for the Weixin agent.

The module keeps market data, deterministic scoring, persistence, and report
formatting separate from Feishu message handling. AKShare is imported lazily so
the existing bot can still start and explain a missing optional dependency.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol


DISCLAIMER = "仅供同花顺模拟盘学习和观察，不构成真实交易建议。"
STOCK_CONTEXT_WORDS = (
    "股票", "个股", "股价", "走势", "趋势", "模拟盘", "值得", "入手",
    "关注", "分析", "看看", "看下", "看一看", "行情",
)
CODE_PATTERN = re.compile(
    r"(?<!\d)(?:(?:sh|sz|bj)[.\s-]?)?([0-9]{6})(?:[.\s-]?(?:sh|sz|bj))?(?!\d)",
    re.IGNORECASE,
)
VALID_A_SHARE_PREFIXES = (
    "000", "001", "002", "003", "159", "300", "301",
    "600", "601", "603", "605", "688",
    "430", "431", "432", "433", "434", "435", "436", "437", "438", "439",
    "8", "920",
)
RISK_NAME_MARKERS = ("ST", "*ST", "退", "N")
RISK_NOTICE_WORDS = ("减持", "处罚", "立案", "诉讼", "风险提示", "亏损", "退市")
POSITIVE_NOTICE_WORDS = ("回购", "增持", "中标", "预增", "扭亏")

# Theme requests are broader than a request to watch one named stock. These
# seed universes keep that workflow useful even when public concept-board APIs
# are unavailable; every run still filters them with current market quotes.
THEME_UNIVERSES = {
    "AI能源与电力": [
        "600406", "000400", "600312", "002028", "601179", "600089",
        "601985", "003816", "600900", "600025", "002837", "002335",
    ],
    "AI上游基础设施": [
        "601138", "300308", "300502", "002463", "300476", "603019",
        "000977", "600845", "002837", "002335", "300442", "603881",
        "300383", "688256",
    ],
}


def _number(value: Any) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        if isinstance(value, str):
            cleaned = value.strip().replace(",", "").replace("%", "")
            multiplier = 1.0
            if cleaned.endswith("亿"):
                cleaned = cleaned[:-1]
                multiplier = 100_000_000
            elif cleaned.endswith("万"):
                cleaned = cleaned[:-1]
                multiplier = 10_000
            result = float(cleaned) * multiplier
        else:
            result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, list):
        return [dict(item) for item in frame]
    if hasattr(frame, "to_dict"):
        return list(frame.to_dict("records"))
    return []


def normalize_stock_code(value: str) -> str | None:
    match = CODE_PATTERN.search(value.strip())
    if not match:
        return None
    code = match.group(1)
    if not code.startswith(VALID_A_SHARE_PREFIXES):
        return None
    return code


def extract_stock_codes(text: str, require_context: bool = True) -> list[str]:
    """Extract unique A-share-shaped codes from text.

    Natural-language detection requires a stock-related word to avoid treating
    dates, verification codes, and other six-digit numbers as stocks.
    """
    lowered = text.lower()
    if require_context and not any(word in lowered for word in STOCK_CONTEXT_WORDS):
        return []
    found: list[str] = []
    for match in CODE_PATTERN.finditer(text):
        code = normalize_stock_code(match.group(0))
        if code and code not in found:
            found.append(code)
    return found


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)


class WatchlistStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _load_unlocked(self) -> dict[str, list[str]]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(user): [
                code for code in values
                if isinstance(code, str) and normalize_stock_code(code)
            ]
            for user, values in raw.items()
            if isinstance(values, list)
        }

    def list(self, user_id: str) -> list[str]:
        with self.lock:
            return list(self._load_unlocked().get(user_id, []))

    def add(self, user_id: str, code: str) -> bool:
        normalized = normalize_stock_code(code)
        if not normalized:
            raise ValueError("不是支持的 A 股代码")
        with self.lock:
            data = self._load_unlocked()
            values = data.setdefault(user_id, [])
            if normalized in values:
                return False
            values.append(normalized)
            _atomic_write_json(self.path, data)
            return True

    def remove(self, user_id: str, code: str) -> bool:
        normalized = normalize_stock_code(code)
        if not normalized:
            raise ValueError("不是支持的 A 股代码")
        with self.lock:
            data = self._load_unlocked()
            values = data.get(user_id, [])
            if normalized not in values:
                return False
            values.remove(normalized)
            if values:
                data[user_id] = values
            else:
                data.pop(user_id, None)
            _atomic_write_json(self.path, data)
            return True


class StockDataProvider(Protocol):
    name: str

    def market_snapshot(self) -> list[dict[str, Any]]: ...

    def quotes(self, codes: list[str]) -> list[dict[str, Any]]: ...

    def history(self, code: str, start: date, end: date) -> list[dict[str, Any]]: ...

    def latest_financials(self, today: date) -> dict[str, dict[str, Any]]: ...

    def recent_notices(self, today: date, days: int = 7) -> dict[str, list[str]]: ...

    def trading_days(self) -> set[date]: ...


class AkshareProvider:
    """AKShare-backed provider with small retries and defensive field mapping."""

    name = "AKShare（公开市场数据，可能有延迟）"

    def __init__(self, retries: int = 2, retry_delay: float = 1.0):
        self.retries = retries
        self.retry_delay = retry_delay
        self._module: Any = None

    def _ak(self) -> Any:
        if self._module is None:
            try:
                import akshare as ak
            except ImportError as exc:
                raise RuntimeError(
                    "股票功能需要 AKShare。请运行：py -m pip install akshare"
                ) from exc
            self._module = ak
        return self._module

    def _call(self, name: str, **kwargs: Any) -> Any:
        function = getattr(self._ak(), name)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return function(**kwargs)
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.retry_delay * (attempt + 1))
        raise RuntimeError(f"AKShare {name} 获取失败：{last_error}") from last_error

    def market_snapshot(self) -> list[dict[str, Any]]:
        return _records(self._call("stock_zh_a_spot_em"))

    def quotes(self, codes: list[str]) -> list[dict[str, Any]]:
        wanted = set(codes)
        return [
            row for row in self.market_snapshot()
            if str(_first(row, "代码", "股票代码") or "").zfill(6) in wanted
        ]

    def history(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        frame = self._call(
            "stock_zh_a_hist",
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        return _records(frame)

    @staticmethod
    def _quarter_dates(today: date) -> list[str]:
        candidates: list[date] = []
        for year in (today.year, today.year - 1):
            for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
                candidate = date(year, month, day)
                if candidate <= today:
                    candidates.append(candidate)
        return [item.strftime("%Y%m%d") for item in sorted(candidates, reverse=True)]

    def latest_financials(self, today: date) -> dict[str, dict[str, Any]]:
        last_error: Exception | None = None
        for report_date in self._quarter_dates(today):
            try:
                rows = _records(self._call("stock_yjbb_em", date=report_date))
            except Exception as exc:
                last_error = exc
                continue
            if not rows:
                continue
            result: dict[str, dict[str, Any]] = {}
            for row in rows:
                code = str(_first(row, "股票代码", "代码") or "").zfill(6)
                if normalize_stock_code(code):
                    item = dict(row)
                    item["_报告期"] = report_date
                    result[code] = item
            if result:
                return result
        if last_error:
            raise RuntimeError(f"最新业绩数据获取失败：{last_error}")
        return {}

    def recent_notices(self, today: date, days: int = 7) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for offset in range(days):
            target = today - timedelta(days=offset)
            if target.weekday() >= 5:
                continue
            try:
                rows = _records(
                    self._call(
                        "stock_notice_report",
                        symbol="全部",
                        date=target.strftime("%Y%m%d"),
                    )
                )
            except Exception:
                continue
            for row in rows:
                code = str(_first(row, "代码", "股票代码") or "").zfill(6)
                title = str(_first(row, "公告标题", "标题") or "").strip()
                if normalize_stock_code(code) and title:
                    result.setdefault(code, []).append(title)
        return result

    def trading_days(self) -> set[date]:
        rows = _records(self._call("tool_trade_date_hist_sina"))
        result: set[date] = set()
        for row in rows:
            value = _first(row, "trade_date", "交易日")
            if value is None:
                continue
            if isinstance(value, datetime):
                result.add(value.date())
            elif isinstance(value, date):
                result.add(value)
            else:
                try:
                    result.add(datetime.fromisoformat(str(value)).date())
                except ValueError:
                    continue
        return result


class EastmoneyPublicProvider:
    """Dependency-free fallback for core quotes and price history."""

    name = "公开行情备用通道（东方财富/新浪，可能有延迟）"

    def __init__(self, timeout: int = 8):
        self.timeout = timeout

    def _get_json(
        self,
        url: str,
        params: dict[str, Any],
        retries: int = 3,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        query = urllib.parse.urlencode(params)
        referer = (
            "https://finance.sina.com.cn/"
            if "sina.com.cn" in url else "https://quote.eastmoney.com/"
        )
        last_error: Exception | None = None
        for attempt in range(retries):
            request = urllib.request.Request(
                f"{url}?{query}",
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                    ),
                    "Referer": referer,
                    "Accept": "application/json,text/plain,*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Connection": "close",
                },
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout,
                ) as response:
                    raw = response.read()
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("gb18030")
                payload = json.loads(text)
                break
            except Exception as exc:
                last_error = exc
                if attempt < retries - 1:
                    time.sleep(attempt + 1)
        else:
            raise RuntimeError(f"公开行情接口连接失败：{last_error}") from last_error
        if not isinstance(payload, (dict, list)):
            raise RuntimeError("公开行情接口返回格式异常")
        return payload

    def market_snapshot(self) -> list[dict[str, Any]]:
        params = {
            "pn": 1,
            "pz": 6000,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12,f14,f2,f3,f5,f6,f8,f10,f9,f20,f21,f23,f24,f25",
        }
        diff = []
        for url in (
            "https://82.push2.eastmoney.com/api/qt/clist/get",
            "https://push2.eastmoney.com/api/qt/clist/get",
            "http://82.push2.eastmoney.com/api/qt/clist/get",
        ):
            try:
                payload = self._get_json(url, params, retries=1)
                if isinstance(payload, dict):
                    diff = (payload.get("data") or {}).get("diff") or []
                if diff:
                    break
            except RuntimeError:
                continue
        if not diff:
            return self._sina_market_snapshot()
        result = []
        for row in diff:
            result.append(
                {
                    "代码": row.get("f12"),
                    "名称": row.get("f14"),
                    "最新价": row.get("f2"),
                    "涨跌幅": row.get("f3"),
                    "成交量": row.get("f5"),
                    "成交额": row.get("f6"),
                    "换手率": row.get("f8"),
                    "量比": row.get("f10"),
                    "市盈率-动态": row.get("f9"),
                    "市净率": row.get("f23"),
                    "总市值": row.get("f20"),
                    "流通市值": row.get("f21"),
                    "60日涨跌幅": row.get("f24"),
                    "年初至今涨跌幅": row.get("f25"),
                }
            )
        return result

    def quotes(self, codes: list[str]) -> list[dict[str, Any]]:
        """Fetch only requested symbols so a single-stock query stays fast."""
        symbols = [
            ("sh" if code.startswith(("5", "6", "9")) else "sz") + code
            for code in codes
        ]
        if not symbols:
            return []
        last_error: Exception | None = None
        for scheme in ("https", "http"):
            request = urllib.request.Request(
                f"{scheme}://hq.sinajs.cn/list={','.join(symbols)}",
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                    ),
                    "Referer": "https://finance.sina.com.cn/",
                    "Accept": "text/plain,*/*",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                text = raw.decode("gb18030", errors="replace")
                rows: list[dict[str, Any]] = []
                for line in text.splitlines():
                    match = re.search(r'hq_str_(?:sh|sz)(\d{6})="(.*)"', line)
                    if not match:
                        continue
                    code, body = match.groups()
                    values = body.split(",")
                    if len(values) < 10 or not values[0]:
                        continue
                    previous = _number(values[2])
                    current = _number(values[3])
                    change = (
                        (current / previous - 1) * 100
                        if current is not None and previous else None
                    )
                    rows.append(
                        {
                            "代码": code,
                            "名称": values[0],
                            "最新价": current,
                            "涨跌幅": change,
                            "成交量": _number(values[8]),
                            "成交额": _number(values[9]),
                            "量比": 1,
                        }
                    )
                if rows:
                    return rows
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"单股报价接口连接失败：{last_error}") from last_error

    def _sina_market_snapshot(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_size = 100
        deadline = time.monotonic() + 180
        for page in range(1, 80):
            if time.monotonic() >= deadline:
                break
            try:
                payload = self._get_json(
                    (
                        "http://vip.stock.finance.sina.com.cn/quotes_service/"
                        "api/json_v2.php/Market_Center.getHQNodeData"
                    ),
                    {
                        "page": page,
                        "num": page_size,
                        "sort": "symbol",
                        "asc": 1,
                        "node": "hs_a",
                        "symbol": "",
                    },
                )
            except RuntimeError:
                if rows:
                    break
                raise
            page_rows = payload if isinstance(payload, list) else []
            if not page_rows:
                break
            rows.extend(page_rows)
            if len(page_rows) < page_size:
                break
            time.sleep(0.2)
        return [
            {
                "代码": row.get("code") or str(row.get("symbol", ""))[-6:],
                "名称": row.get("name"),
                "最新价": row.get("trade"),
                "涨跌幅": row.get("changepercent"),
                "成交量": row.get("volume"),
                "成交额": row.get("amount"),
                "换手率": row.get("turnoverratio"),
                "量比": 1,
                "市盈率-动态": row.get("per"),
                "市净率": row.get("pb"),
            }
            for row in rows
        ]

    @staticmethod
    def _market_id(code: str) -> str:
        return "1" if code.startswith(("5", "6", "9")) else "0"

    def history(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        try:
            payload = self._get_json(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                {
                    "secid": f"{self._market_id(code)}.{code}",
                    "klt": 101,
                    "fqt": 1,
                    "lmt": 500,
                    "end": end.strftime("%Y%m%d"),
                    "iscca": 1,
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                },
            )
            klines = (payload.get("data") or {}).get("klines") or []
        except RuntimeError:
            return self._sina_history(code, start)
        result = []
        for line in klines:
            values = str(line).split(",")
            if len(values) < 7 or values[0] < start.isoformat():
                continue
            result.append(
                {
                    "日期": values[0],
                    "开盘": values[1],
                    "收盘": values[2],
                    "最高": values[3],
                    "最低": values[4],
                    "成交量": values[5],
                    "成交额": values[6],
                }
            )
        return result

    def _sina_history(self, code: str, start: date) -> list[dict[str, Any]]:
        market = "sh" if code.startswith(("5", "6", "9")) else "sz"
        query = urllib.parse.urlencode(
            {
                "symbol": f"{market}{code}",
                "scale": 240,
                "ma": "no",
                "datalen": 500,
            }
        )
        request = urllib.request.Request(
            (
                "http://quotes.sina.cn/cn/api/jsonp_v2.php/"
                f"var%20_stock_data=/CN_MarketDataService.getKLineData?{query}"
            ),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                ),
                "Referer": "https://finance.sina.com.cn/",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
            start_index = raw.find("([")
            end_index = raw.rfind("]);")
            if start_index < 0 or end_index < 0:
                raise ValueError("JSONP 格式异常")
            rows = json.loads(raw[start_index + 1:end_index + 1])
        except Exception as exc:
            raise RuntimeError(f"备用历史行情接口连接失败：{exc}") from exc
        result = []
        for row in rows:
            day = str(row.get("day") or "")
            if day < start.isoformat():
                continue
            result.append(
                {
                    "日期": day,
                    "开盘": row.get("open"),
                    "收盘": row.get("close"),
                    "最高": row.get("high"),
                    "最低": row.get("low"),
                    "成交量": row.get("volume"),
                }
            )
        return result

    def latest_financials(self, today: date) -> dict[str, dict[str, Any]]:
        # Positive dynamic P/E in the snapshot already filters currently
        # profitable companies. Detailed financials remain an AKShare upgrade.
        return {}

    def recent_notices(self, today: date, days: int = 7) -> dict[str, list[str]]:
        return {}

    def trading_days(self) -> set[date]:
        start = date.today() - timedelta(days=370)
        end = date.today() + timedelta(days=370)
        current = start
        result = set()
        while current <= end:
            if current.weekday() < 5:
                result.add(current)
            current += timedelta(days=1)
        return result


class TencentCsi300Provider:
    """Bounded market data over a reproducible CSI 300 universe.

    AKShare refreshes the constituent snapshot separately. Runtime quote and
    adjusted-history requests use Tencent's public endpoints, which support
    bounded timeouts and avoid a blocking full-market request.
    """

    def __init__(
        self,
        timeout: int = 8,
        universe_path: str | Path | None = None,
    ):
        self.timeout = timeout
        self.universe_path = Path(universe_path or (
            Path(__file__).resolve().parent.parent / "data" / "csi300-universe.json"
        ))
        self._universe_payload: dict[str, Any] | None = None
        self._fallback = EastmoneyPublicProvider(timeout=min(timeout, 5))

    @property
    def name(self) -> str:
        payload = self._load_universe()
        as_of = str(payload.get("as_of") or "未知日期")
        return f"腾讯公开行情 + 沪深300成分股（成分截至 {as_of}，可能有延迟）"

    def _load_universe(self) -> dict[str, Any]:
        if self._universe_payload is not None:
            return self._universe_payload
        try:
            with self.universe_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(
                "缺少沪深300股票池，请运行：py tools/refresh_stock_universe.py"
            ) from exc
        stocks = payload.get("stocks") if isinstance(payload, dict) else None
        if not isinstance(stocks, list) or len(stocks) < 250:
            raise RuntimeError("沪深300股票池不完整，请重新运行股票池更新命令")
        self._universe_payload = payload
        return payload

    def _request_text(self, url: str, encoding: str = "utf-8") -> str:
        last_error: Exception | None = None
        for attempt in range(2):
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Referer": "https://gu.qq.com/",
                    "Accept": "*/*",
                    "Connection": "close",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read().decode(encoding, errors="replace")
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.5)
        raise RuntimeError(f"腾讯行情接口连接失败：{last_error}") from last_error

    @staticmethod
    def _symbol(code: str) -> str:
        return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code

    def _quote_chunk(self, codes: list[str]) -> list[dict[str, Any]]:
        symbols = ",".join(self._symbol(code) for code in codes)
        text = self._request_text(
            f"https://qt.gtimg.cn/q={symbols}",
            encoding="gb18030",
        )
        result: list[dict[str, Any]] = []
        for match in re.finditer(r'v_(?:sh|sz)(\d{6})="(.*?)";', text):
            code, body = match.groups()
            values = body.split("~")
            if len(values) < 50:
                continue
            price = _number(values[3])
            previous = _number(values[4])
            change = _number(values[32])
            if change is None and price is not None and previous:
                change = (price / previous - 1) * 100
            amount_wan = _number(values[37])
            total_cap_yi = _number(values[44])
            float_cap_yi = _number(values[45])
            result.append({
                "代码": code,
                "名称": values[1],
                "最新价": price,
                "涨跌幅": change,
                "成交量": (_number(values[36]) or 0) * 100,
                "成交额": amount_wan * 10_000 if amount_wan is not None else None,
                "换手率": _number(values[38]),
                "量比": _number(values[49]),
                "市盈率-动态": _number(values[39]),
                "市净率": _number(values[46]),
                "总市值": total_cap_yi * 100_000_000 if total_cap_yi is not None else None,
                "流通市值": float_cap_yi * 100_000_000 if float_cap_yi is not None else None,
            })
        return result

    def quotes(self, codes: list[str]) -> list[dict[str, Any]]:
        normalized = list(dict.fromkeys(
            code for code in codes if normalize_stock_code(code)
        ))
        if not normalized:
            return []
        result: list[dict[str, Any]] = []
        try:
            for offset in range(0, len(normalized), 80):
                result.extend(self._quote_chunk(normalized[offset:offset + 80]))
        except Exception:
            if len(normalized) <= 5:
                return self._fallback.quotes(normalized)
            raise
        industries = {
            str(item.get("code") or "").zfill(6): str(item.get("industry") or "").strip()
            for item in self._load_universe().get("stocks", [])
            if isinstance(item, dict)
        }
        for row in result:
            industry = industries.get(str(row.get("代码") or "").zfill(6))
            if industry:
                row["行业"] = industry
        return result

    def market_snapshot(self) -> list[dict[str, Any]]:
        payload = self._load_universe()
        codes = [
            str(item.get("code") or "").zfill(6)
            for item in payload["stocks"]
            if isinstance(item, dict)
        ]
        rows = self.quotes(codes)
        if len(rows) < 250:
            raise RuntimeError(f"沪深300实时行情仅取得 {len(rows)} 只，已停止本次筛选")
        return rows

    def history(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({
            "param": (
                f"{self._symbol(code)},day,{start.isoformat()},"
                f"{end.isoformat()},500,qfq"
            ),
        })
        try:
            payload = json.loads(self._request_text(
                f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?{params}"
            ))
            stock = (payload.get("data") or {}).get(self._symbol(code)) or {}
            rows = stock.get("qfqday") or stock.get("day") or []
            result = []
            for values in rows:
                if not isinstance(values, list) or len(values) < 6:
                    continue
                result.append({
                    "日期": values[0],
                    "开盘": values[1],
                    "收盘": values[2],
                    "最高": values[3],
                    "最低": values[4],
                    "成交量": values[5],
                })
            if result:
                return result
        except Exception:
            pass
        return self._fallback._sina_history(code, start)

    def latest_financials(self, today: date) -> dict[str, dict[str, Any]]:
        # PE/PB still participate in valuation. Missing quality data is neutral.
        return {}

    def recent_notices(self, today: date, days: int = 7) -> dict[str, list[str]]:
        return {}

    def trading_days(self) -> set[date]:
        return self._fallback.trading_days()


class AutoStockDataProvider:
    """Use the bounded CSI 300 provider and retain a small-query fallback."""

    def __init__(self):
        self.primary = TencentCsi300Provider()
        self.fallback = EastmoneyPublicProvider()
        self.active: StockDataProvider = self.primary

    @property
    def name(self) -> str:
        return self.active.name

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self.active, method)(*args, **kwargs)
        except Exception:
            if self.active is self.fallback:
                raise
            if method == "market_snapshot":
                raise
            self.active = self.fallback
            return getattr(self.active, method)(*args, **kwargs)

    def market_snapshot(self) -> list[dict[str, Any]]:
        return self._call("market_snapshot")

    def quotes(self, codes: list[str]) -> list[dict[str, Any]]:
        return self._call("quotes", codes)

    def history(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        return self._call("history", code, start, end)

    def latest_financials(self, today: date) -> dict[str, dict[str, Any]]:
        return self._call("latest_financials", today)

    def recent_notices(self, today: date, days: int = 7) -> dict[str, list[str]]:
        return self._call("recent_notices", today, days)

    def trading_days(self) -> set[date]:
        return self._call("trading_days")


@dataclass
class StockAnalysis:
    code: str
    name: str
    score: int
    trend: str
    price: float
    data_date: str
    reasons: list[str]
    risks: list[str]
    facts: list[str]
    factor_scores: dict[str, int]
    metrics: dict[str, Any]
    stop_loss: float
    first_target: float


class StockResearchService:
    """Screen the A-share market and format beginner-friendly reports."""

    def __init__(
        self,
        provider: StockDataProvider | None = None,
        cache_seconds: int = 4 * 60 * 60,
        shortlist_size: int = 36,
        minimum_market_size: int = 250,
    ):
        self.provider = provider or AutoStockDataProvider()
        self.cache_seconds = cache_seconds
        self.shortlist_size = shortlist_size
        self.minimum_market_size = minimum_market_size
        self._lock = threading.Lock()
        self._market_cache: tuple[float, date, list[StockAnalysis]] | None = None
        self._snapshot_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
        self._financial_cache: tuple[date, dict[str, dict[str, Any]]] | None = None
        self._notice_cache: tuple[date, dict[str, list[str]]] | None = None
        self._calendar_cache: tuple[float, set[date]] | None = None

    @staticmethod
    def _snapshot_code(row: dict[str, Any]) -> str:
        return str(_first(row, "代码", "股票代码") or "").zfill(6)

    @staticmethod
    def _snapshot_name(row: dict[str, Any]) -> str:
        return str(_first(row, "名称", "股票简称") or "").strip()

    def _snapshot(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        if self._snapshot_cache and now - self._snapshot_cache[0] < self.cache_seconds:
            return self._snapshot_cache[1]
        rows = self.provider.market_snapshot()
        result = {
            self._snapshot_code(row): row
            for row in rows
            if normalize_stock_code(self._snapshot_code(row))
        }
        if len(result) < self.minimum_market_size:
            raise RuntimeError(
                f"全市场行情只取得 {len(result)} 只，数据不完整，已停止本次筛选"
            )
        self._snapshot_cache = (now, result)
        return result

    def _financials(self, today: date) -> dict[str, dict[str, Any]]:
        if self._financial_cache and self._financial_cache[0] == today:
            return self._financial_cache[1]
        try:
            data = self.provider.latest_financials(today)
        except Exception:
            data = {}
        self._financial_cache = (today, data)
        return data

    def _notices(self, today: date) -> dict[str, list[str]]:
        if self._notice_cache and self._notice_cache[0] == today:
            return self._notice_cache[1]
        try:
            data = self.provider.recent_notices(today)
        except Exception:
            data = {}
        self._notice_cache = (today, data)
        return data

    @staticmethod
    def _eligible_snapshot(row: dict[str, Any]) -> bool:
        name = StockResearchService._snapshot_name(row).upper()
        price = _number(_first(row, "最新价", "收盘"))
        change = _number(_first(row, "涨跌幅"))
        amount = _number(_first(row, "成交额"))
        turnover = _number(_first(row, "换手率"))
        pe = _number(_first(row, "市盈率-动态", "动态市盈率", "市盈率"))
        pb = _number(_first(row, "市净率"))
        if not name or any(marker in name for marker in RISK_NAME_MARKERS):
            return False
        if price is None or price < 3 or price > 300:
            return False
        if change is None or abs(change) > 6:
            return False
        if amount is None or amount < 200_000_000:
            return False
        if turnover is not None and (turnover < 0.2 or turnover > 10):
            return False
        if pe is not None and (pe <= 0 or pe > 80):
            return False
        if pb is not None and (pb <= 0 or pb > 10):
            return False
        return True

    @staticmethod
    def _preliminary_score(row: dict[str, Any]) -> float:
        change = _number(_first(row, "涨跌幅")) or 0
        amount = _number(_first(row, "成交额")) or 0
        turnover = _number(_first(row, "换手率")) or 0
        pe = _number(_first(row, "市盈率-动态", "动态市盈率", "市盈率"))
        pb = _number(_first(row, "市净率"))
        market_cap = _number(_first(row, "总市值", "流通市值"))
        return60 = _number(_first(row, "60日涨跌幅"))
        score = min(math.log10(max(amount, 1)) * 5, 48)
        if market_cap is not None and 5_000_000_000 <= market_cap <= 500_000_000_000:
            score += 8
        if pe is not None:
            score += max(0, 10 - abs(math.log(max(pe, 0.1) / 22)) * 6)
        if pb is not None:
            score += max(0, 6 - abs(math.log(max(pb, 0.1) / 2.5)) * 4)
        if return60 is not None and -5 <= return60 <= 35:
            score += 6
        score -= max(0, abs(change) - 2) * 2
        score -= max(0, turnover - 5)
        return score

    @staticmethod
    def _history_values(
        rows: list[dict[str, Any]],
        *fields: str,
    ) -> list[float]:
        result: list[float] = []
        for row in rows:
            value = _number(_first(row, *fields))
            if value is not None:
                result.append(value)
        return result

    @staticmethod
    def _percent_change(new: float, old: float) -> float:
        return (new / old - 1) * 100 if old else 0

    def _analyze(
        self,
        code: str,
        snapshot: dict[str, Any],
        history: list[dict[str, Any]],
        financial: dict[str, Any] | None,
        notices: list[str],
    ) -> StockAnalysis:
        history = sorted(history, key=lambda row: str(_first(row, "日期", "date") or ""))
        closes = self._history_values(history, "收盘", "close")
        volumes = self._history_values(history, "成交量", "volume")
        if len(closes) < 21:
            raise ValueError("可用历史行情不足 21 个交易日")

        latest = closes[-1]
        long_window = min(60, len(closes))
        ma5 = statistics.fmean(closes[-5:])
        ma20 = statistics.fmean(closes[-20:])
        ma60 = statistics.fmean(closes[-long_window:])
        return5 = self._percent_change(latest, closes[-6])
        return20 = self._percent_change(latest, closes[-21])
        return60 = self._percent_change(latest, closes[-long_window])
        return120 = (
            self._percent_change(latest, closes[-121])
            if len(closes) >= 121 else None
        )
        high60 = max(closes[-long_window:])
        high20 = max(closes[-20:])
        drawdown = self._percent_change(latest, high60)
        drawdown20 = self._percent_change(latest, high20)
        daily_returns = [
            self._percent_change(closes[index], closes[index - 1])
            for index in range(len(closes) - 19, len(closes))
        ]
        volatility = statistics.pstdev(daily_returns)
        true_ranges: list[float] = []
        for index in range(max(1, len(history) - 14), len(history)):
            high = _number(_first(history[index], "最高", "high"))
            low = _number(_first(history[index], "最低", "low"))
            previous_close = _number(_first(history[index - 1], "收盘", "close"))
            if high is None or low is None or previous_close is None:
                continue
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        atr = statistics.fmean(true_ranges) if true_ranges else latest * max(volatility, 1) / 100
        atr_percent = atr / latest * 100 if latest else 0
        volume_ratio = None
        if len(volumes) >= 6 and statistics.fmean(volumes[-6:-1]) > 0:
            volume_ratio = volumes[-1] / statistics.fmean(volumes[-6:-1])

        score = 45
        reasons: list[str] = []
        risks: list[str] = []
        facts: list[str] = []
        if long_window < 60:
            risks.append(f"上市历史较短，目前只有 {long_window} 个交易日数据，长期趋势判断可信度较低")

        if latest > ma20 > ma60:
            score += 18
            reasons.append("最近一到三个月整体保持向上")
            trend = "稳步向上"
        elif latest > ma20:
            score += 8
            reasons.append("最近一个月有所转强")
            trend = "正在转强"
        elif latest < ma20 < ma60:
            score -= 18
            risks.append("最近一到三个月整体仍在走弱")
            trend = "偏弱"
        else:
            trend = "来回震荡"
            risks.append("方向还不够明确，价格上下反复")

        if latest > ma5 > ma20:
            score += 8
            reasons.append("最近几天的上升状态仍在延续")
        if 2 <= return20 <= 15:
            score += 8
            reasons.append(f"近一个月上涨约 {return20:.1f}%，速度不算极端")
        elif return20 > 20:
            score -= 12
            risks.append(f"近一个月已上涨约 {return20:.1f}%，短期追高风险较大")
        elif return20 < -10:
            score -= 10
            risks.append(f"近一个月下跌约 {abs(return20):.1f}%，尚未明显稳定")

        if -12 <= drawdown <= -3:
            score += 4
            reasons.append("距离近三个月最高点仍有一定空间")
        elif drawdown > -1:
            risks.append("价格接近近三个月高位，需要防止冲高回落")

        if volume_ratio is not None:
            if 1.15 <= volume_ratio <= 2.5:
                score += 7
                reasons.append("最新成交比近期平时更活跃")
            elif volume_ratio >= 3:
                score -= 4
                risks.append("成交突然放大很多，短期波动可能加剧")

        if volatility <= 2.5:
            score += 5
            reasons.append("近期每天的价格波动相对可控")
        elif volatility >= 5:
            score -= 8
            risks.append("近期每天涨跌幅度较大，新手模拟时也要谨慎")

        pe = _number(_first(snapshot, "市盈率-动态", "动态市盈率", "市盈率"))
        pb = _number(_first(snapshot, "市净率"))
        amount = _number(_first(snapshot, "成交额"))
        if pe is not None:
            facts.append(f"按当前利润计算的价格倍数约 {pe:.1f}")
            if 5 <= pe <= 45:
                score += 4
            elif pe > 70:
                score -= 5
                risks.append("按当前利润计算，价格水平偏高")
        if pb is not None:
            facts.append(f"股价相对公司净资产的倍数约 {pb:.1f}")

        revenue_growth = None
        profit_growth = None
        roe = None
        if financial:
            revenue_growth = _number(
                _first(
                    financial,
                    "营业总收入同比增长",
                    "营业收入-同比增长",
                    "营业收入同比增长",
                )
            )
            profit_growth = _number(
                _first(
                    financial,
                    "净利润同比增长",
                    "净利润-同比增长",
                    "归属净利润同比增长",
                )
            )
            roe = _number(_first(financial, "净资产收益率", "加权净资产收益率"))
            if revenue_growth is not None:
                facts.append(f"最近一期营业收入同比 {revenue_growth:+.1f}%")
                if revenue_growth > 5:
                    score += 5
                    reasons.append("公司最近一期收入仍在增长")
                elif revenue_growth < -10:
                    score -= 7
                    risks.append("公司最近一期收入下降较明显")
            if profit_growth is not None:
                facts.append(f"最近一期净利润同比 {profit_growth:+.1f}%")
                if profit_growth > 5:
                    score += 7
                    reasons.append("公司最近一期利润仍在增长")
                elif profit_growth < -10:
                    score -= 10
                    risks.append("公司最近一期利润下降较明显")
            if roe is not None:
                facts.append(f"最近一期公司使用自有资本的盈利能力约 {roe:.1f}%")

        relevant_notices = notices[:5]
        for title in relevant_notices:
            if any(word in title for word in RISK_NOTICE_WORDS):
                score -= 8
                risks.append(f"近期公告需留意：{title[:34]}")
                break
        else:
            for title in relevant_notices:
                if any(word in title for word in POSITIVE_NOTICE_WORDS):
                    score += 4
                    reasons.append(f"近期公告出现积极事项：{title[:34]}")
                    break

        if not relevant_notices:
            facts.append("近期公告信息未取得或没有匹配记录")

        data_date = str(_first(history[-1], "日期", "date") or date.today())
        stop_distance_percent = max(5.0, min(10.0, atr_percent * 2.2))
        stop_loss = latest * (1 - stop_distance_percent / 100)
        first_target = latest + (latest - stop_loss) * 2
        value_factor = None
        if pe is not None and pe > 0 and pb is not None and pb > 0:
            value_factor = -abs(math.log(pe / 22)) * 0.65 - abs(math.log(pb / 2.5)) * 0.35
        quality_values = [value for value in (roe, revenue_growth, profit_growth) if value is not None]
        quality_factor = None
        if quality_values:
            quality_factor = (
                (roe or 0) * 0.5
                + (revenue_growth or 0) * 0.2
                + (profit_growth or 0) * 0.3
            )
        momentum_factor = return60 * 0.55 + (return120 if return120 is not None else return60) * 0.45
        momentum_factor -= max(0.0, return20 - 18) * 1.8
        momentum_6_1 = self._percent_change(closes[-21], closes[-121]) if len(closes) >= 121 else momentum_factor
        if len(closes) >= 253:
            momentum_12_1 = self._percent_change(closes[-21], closes[-253])
            momentum_6_1 = momentum_6_1 * 0.55 + momentum_12_1 * 0.45
        value_cheap_factor = None
        if pe is not None and pe > 0 and pb is not None and pb > 0:
            value_cheap_factor = -math.log(pe) * 0.65 - math.log(pb) * 0.35
        trend_factor = self._percent_change(latest, ma20) + self._percent_change(ma20, ma60)
        return StockAnalysis(
            code=code,
            name=self._snapshot_name(snapshot),
            score=max(0, min(100, round(score))),
            trend=trend,
            price=latest,
            data_date=data_date,
            reasons=reasons[:4] or ["目前没有足够明显的积极变化"],
            risks=risks[:3] or ["暂未发现突出的短期风险信号，但仍可能随市场变化"],
            facts=facts[:4],
            factor_scores={},
            metrics={
                "rule_score": float(max(0, min(100, round(score)))),
                "trend_factor": trend_factor,
                "momentum_factor": momentum_factor,
                "momentum_6_1_factor": momentum_6_1,
                "quality_factor": quality_factor,
                "value_factor": value_factor,
                "value_cheap_factor": value_cheap_factor,
                "low_volatility_factor": -volatility,
                "liquidity_factor": math.log10(max(amount or 1, 1)),
                "risk_factor": -atr_percent + drawdown * 0.08,
                "return5": return5,
                "return20": return20,
                "return60": return60,
                "return120": return120,
                "ma20": ma20,
                "ma60": ma60,
                "drawdown20": drawdown20,
                "drawdown60": drawdown,
                "volatility20": volatility,
                "atr_percent": atr_percent,
                "pe": pe,
                "pb": pb,
                "roe": roe,
                "revenue_growth": revenue_growth,
                "profit_growth": profit_growth,
                "industry": str(_first(snapshot, "行业", "industry") or "").strip(),
            },
            stop_loss=round(stop_loss, 2),
            first_target=round(first_target, 2),
        )

    @staticmethod
    def _percentile_scores(
        analyses: list[StockAnalysis],
        metric: str,
    ) -> dict[str, int]:
        values = [
            float(item.metrics[metric])
            for item in analyses
            if item.metrics.get(metric) is not None
        ]
        if len(values) < 2:
            return {item.code: 50 for item in analyses}
        result: dict[str, int] = {}
        for item in analyses:
            raw = item.metrics.get(metric)
            if raw is None:
                result[item.code] = 50
                continue
            lower = sum(1 for value in values if value < float(raw))
            equal = sum(1 for value in values if value == float(raw))
            percentile = (lower + (equal - 1) / 2) / (len(values) - 1)
            result[item.code] = round(max(0, min(1, percentile)) * 100)
        return result

    def _rank_analyses(self, analyses: list[StockAnalysis]) -> list[StockAnalysis]:
        factors = {
            "趋势": ("trend_factor", 0.225),
            "中期动量": ("momentum_6_1_factor", 0.20),
            "基本面质量": ("quality_factor", 0.10),
            "估值合理度": ("value_cheap_factor", 0.175),
            "低波动": ("low_volatility_factor", 0.175),
            "流动性": ("liquidity_factor", 0.05),
            "风险控制": ("risk_factor", 0.075),
        }
        ranked = {
            name: self._percentile_scores(analyses, metric)
            for name, (metric, _weight) in factors.items()
        }
        for item in analyses:
            item.factor_scores = {
                name: scores[item.code]
                for name, scores in ranked.items()
            }
            factor_score = sum(
                item.factor_scores[name] * weight
                for name, (_metric, weight) in factors.items()
            )
            rule_score = float(item.metrics.get("rule_score") or 50)
            final_score = factor_score * 0.75 + rule_score * 0.25
            if float(item.metrics.get("return5") or 0) > 9:
                final_score -= 8
                item.risks.append("近 5 个交易日涨速过快，不适合追价")
            if float(item.metrics.get("return20") or 0) > 22:
                final_score -= 8
            item.score = max(0, min(100, round(final_score)))

            leaders = sorted(
                item.factor_scores.items(),
                key=lambda pair: pair[1],
                reverse=True,
            )
            for name, value in leaders[:2]:
                if value >= 70:
                    item.reasons.append(f"{name}在本轮候选中位于前列（{value}/100）")
            laggards = sorted(item.factor_scores.items(), key=lambda pair: pair[1])
            for name, value in laggards[:1]:
                if value <= 25:
                    item.risks.append(f"{name}在本轮候选中偏弱（{value}/100）")
            item.reasons = list(dict.fromkeys(item.reasons))[:5]
            item.risks = list(dict.fromkeys(item.risks))[:4]
        analyses.sort(key=lambda item: item.score, reverse=True)
        return analyses

    @staticmethod
    def _diversified_candidates(
        analyses: list[StockAnalysis],
        limit: int,
    ) -> list[StockAnalysis]:
        """Keep a short recommendation list from becoming one industry bet."""
        if limit <= 0:
            return []
        per_industry = max(1, math.ceil(limit / 3))
        counts: dict[str, int] = {}
        selected: list[StockAnalysis] = []
        for item in analyses:
            industry = str(item.metrics.get("industry") or f"未分类:{item.code}")
            if counts.get(industry, 0) >= per_industry:
                continue
            selected.append(item)
            counts[industry] = counts.get(industry, 0) + 1
            if len(selected) >= limit:
                break
        return selected

    def _analyze_code(
        self,
        code: str,
        snapshot: dict[str, Any],
        today: date,
        financials: dict[str, dict[str, Any]],
        notices: dict[str, list[str]],
    ) -> StockAnalysis:
        history = self.provider.history(code, today - timedelta(days=380), today)
        return self._analyze(
            code,
            snapshot,
            history,
            financials.get(code),
            notices.get(code, []),
        )

    def screen_market(
        self,
        today: date | None = None,
        force_refresh: bool = False,
    ) -> list[StockAnalysis]:
        today = today or date.today()
        now = time.time()
        with self._lock:
            if force_refresh:
                self._market_cache = None
                self._snapshot_cache = None
                self._notice_cache = None
            if (
                self._market_cache
                and self._market_cache[1] == today
                and now - self._market_cache[0] < self.cache_seconds
            ):
                return list(self._market_cache[2])

            snapshot = self._snapshot()
            eligible = [
                (code, row)
                for code, row in snapshot.items()
                if self._eligible_snapshot(row)
            ]
            eligible.sort(
                key=lambda item: self._preliminary_score(item[1]),
                reverse=True,
            )
            financials = self._financials(today)
            notices = self._notices(today)
            analyses: list[StockAnalysis] = []
            candidates = eligible[:self.shortlist_size]
            with ThreadPoolExecutor(
                max_workers=min(6, len(candidates) or 1),
                thread_name_prefix="stock-history",
            ) as executor:
                futures = {
                    executor.submit(
                        self._analyze_code,
                        code,
                        row,
                        today,
                        financials,
                        notices,
                    ): code
                    for code, row in candidates
                }
                for future in as_completed(futures):
                    try:
                        analyses.append(future.result())
                    except Exception:
                        continue
            analyses = self._rank_analyses(analyses)
            self._market_cache = (now, today, analyses)
            return list(analyses)

    def analyze_codes(
        self,
        codes: list[str],
        today: date | None = None,
        max_codes: int = 5,
    ) -> tuple[list[StockAnalysis], list[str]]:
        today = today or date.today()
        selected = list(dict.fromkeys(codes))[:max(1, max_codes)]
        snapshot = self._snapshot_for_codes(selected)
        financials = self._financials(today)
        notices = self._notices(today)
        analyses: list[StockAnalysis] = []
        errors: list[str] = []
        for code in selected:
            if code not in snapshot:
                errors.append(f"{code}：没有在当前 A 股代码表中找到")
                continue
            try:
                analyses.append(
                    self._analyze_code(
                        code,
                        snapshot[code],
                        today,
                        financials,
                        notices,
                    )
                )
            except Exception as exc:
                errors.append(f"{code}：{exc}")
        return analyses, errors

    def _watchlist_priority_codes(
        self,
        codes: list[str],
        limit: int,
    ) -> list[str]:
        """Scan the whole watchlist before choosing a bounded deep-analysis set."""
        normalized = list(dict.fromkeys(
            code for code in codes if normalize_stock_code(code)
        ))[:60]
        snapshot = self._snapshot_for_codes(normalized)
        ranked = sorted(
            (
                (code, row)
                for code, row in snapshot.items()
                if self._snapshot_name(row)
            ),
            key=lambda item: (
                abs(_number(_first(item[1], "涨跌幅")) or 0),
                _number(_first(item[1], "成交额")) or 0,
            ),
            reverse=True,
        )
        return [code for code, _row in ranked[:max(1, limit)]]

    def theme_candidates(
        self,
        themes: list[str],
        limit_per_theme: int = 5,
    ) -> dict[str, list[dict[str, Any]]]:
        """Filter stable theme universes with current quotes.

        This deliberately returns an observation list, not a buy signal. Deep
        history/factor analysis remains part of the scheduled watch report.
        """
        result: dict[str, list[dict[str, Any]]] = {}
        for theme in dict.fromkeys(str(item).strip() for item in themes):
            codes = THEME_UNIVERSES.get(theme, [])
            if not codes:
                result[theme] = []
                continue
            snapshot = self._snapshot_for_codes(codes)
            eligible = [
                (code, row)
                for code, row in snapshot.items()
                if self._eligible_snapshot(row)
            ]
            eligible.sort(
                key=lambda item: self._preliminary_score(item[1]),
                reverse=True,
            )
            result[theme] = [
                {
                    "code": code,
                    "name": self._snapshot_name(row),
                    "price": _number(_first(row, "最新价", "收盘")),
                    "change": _number(_first(row, "涨跌幅")),
                }
                for code, row in eligible[:max(1, limit_per_theme)]
            ]
        return result

    def _snapshot_for_codes(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        if self._snapshot_cache:
            cached = self._snapshot_cache[1]
            if all(code in cached for code in codes):
                return {code: cached[code] for code in codes}
        quote_method = getattr(self.provider, "quotes", None)
        rows = quote_method(codes) if callable(quote_method) else self.provider.market_snapshot()
        return {
            self._snapshot_code(row): row
            for row in rows
            if self._snapshot_code(row) in codes
        }

    def stock_identity(self, code: str) -> tuple[str, str] | None:
        normalized = normalize_stock_code(code)
        if not normalized:
            return None
        row = self._snapshot_for_codes([normalized]).get(normalized)
        if not row:
            return None
        return normalized, self._snapshot_name(row)

    def is_trading_day(self, target: date) -> bool:
        now = time.time()
        if not self._calendar_cache or now - self._calendar_cache[0] > 24 * 60 * 60:
            try:
                self._calendar_cache = (now, self.provider.trading_days())
            except Exception:
                return target.weekday() < 5
        return target in self._calendar_cache[1]

    @staticmethod
    def _format_item(item: StockAnalysis, index: int) -> str:
        factor_line = "｜".join(
            f"{name}{value}"
            for name, value in item.factor_scores.items()
            if name in {"趋势", "中期动量", "基本面质量", "估值合理度", "低波动"}
        )
        lines = [
            f"{index}. {item.name}（{item.code}）",
            f"研究分：{item.score}/100｜趋势：{item.trend}｜参考价：{item.price:.2f}",
            f"因子：{factor_line}",
            "为什么值得看：" + "；".join(item.reasons),
            "需要小心：" + "；".join(item.risks),
            f"模拟风控线：{item.stop_loss:.2f}｜第一观察目标：{item.first_target:.2f}",
        ]
        if item.facts:
            lines.append("补充数据：" + "；".join(item.facts))
        return "\n".join(lines)

    def format_market_report(
        self,
        analyses: list[StockAnalysis],
        limit: int = 5,
    ) -> str:
        candidates = self._diversified_candidates(
            [item for item in analyses if item.score >= 72],
            min(limit, 3),
        )
        if not candidates:
            return (
                "今日 A 股模拟关注报告\n\n"
                "今天没有筛出足够明确的新候选。少做一次判断，也比为了凑数随便挑股票更好。\n\n"
                f"数据来源：{self.provider.name}\n{DISCLAIMER}"
            )
        data_date = candidates[0].data_date
        lines = [
            f"A 股收盘模拟关注名单｜数据日期：{data_date}",
            "",
            "下面是多因子排名达到门槛的模拟盘候选。已先过滤 ST、退市风险、"
            "成交太少、单日异动和估值极端标的；不足三只时不会凑数。",
            "",
        ]
        for index, item in enumerate(candidates, 1):
            lines.extend([self._format_item(item, index), ""])
        lines.extend(
            [
                "怎么用：这不是立即追价指令。先加入模拟盘观察，再用 /stock picks "
                "结合你的模拟本金生成分批数量。",
                "",
                f"数据来源：{self.provider.name}",
                DISCLAIMER,
            ]
        )
        return "\n".join(lines)

    def market_report(
        self,
        today: date | None = None,
        limit: int = 5,
        force_refresh: bool = False,
    ) -> str:
        return self.format_market_report(
            self.screen_market(today, force_refresh=force_refresh),
            limit,
        )

    def code_report(self, codes: list[str], today: date | None = None) -> str:
        analyses, errors = self.analyze_codes(codes, today)
        lines = ["A 股个股观察", ""]
        for index, item in enumerate(analyses, 1):
            lines.extend([self._format_item(item, index), ""])
        if errors:
            lines.append("未完成：" + "；".join(errors))
            lines.append("")
        lines.extend([f"数据来源：{self.provider.name}", DISCLAIMER])
        return "\n".join(lines)

    def watchlist_report(
        self,
        codes: list[str],
        today: date | None = None,
        limit: int = 5,
    ) -> str:
        priority_codes = self._watchlist_priority_codes(
            codes,
            limit=max(limit + 3, 8),
        )
        analyses, errors = self.analyze_codes(
            priority_codes,
            today,
            max_codes=len(priority_codes),
        )
        ranked = sorted(
            analyses,
            key=lambda item: (
                abs(float(item.metrics.get("return5") or 0)),
                abs(float(item.score) - 50),
            ),
            reverse=True,
        )
        shown = ranked[: max(1, limit)]
        lines = [
            f"观察池共 {len(codes)} 只，本次展示信号变化最明显的 {len(shown)} 只。",
            "关注不等于买入；没有达到策略条件时只观察。",
            "",
        ]
        for index, item in enumerate(shown, 1):
            lines.extend([self._format_item(item, index), ""])
        if errors:
            lines.append("未完成：" + "；".join(errors))
            lines.append("")
        lines.extend([f"数据来源：{self.provider.name}", DISCLAIMER])
        return "\n".join(lines)

    @staticmethod
    def _round_lot(quantity: float) -> int:
        return max(0, int(quantity // 100) * 100)

    def paper_pick_report(
        self,
        portfolio: dict[str, Any],
        today: date | None = None,
    ) -> str:
        analyses = self.screen_market(today)
        held_codes = set((portfolio.get("positions") or {}).keys())
        slots = max(0, 3 - len(held_codes))
        candidate_limit = slots if slots > 0 else 2
        candidates = self._diversified_candidates(
            [
                item for item in analyses
                if item.score >= 72 and item.code not in held_codes
            ],
            candidate_limit,
        )
        capital = float(portfolio.get("capital") or 100_000)
        cash = float(portfolio.get("cash", capital))
        default_note = "（当前使用默认 10 万元）" if portfolio.get("capital_is_default", True) else ""
        lines = [
            f"同花顺模拟盘候选｜本金 {capital:,.0f} 元{default_note}",
            f"可用模拟现金：{cash:,.0f} 元｜已有持仓：{len(held_codes)}/3",
            "",
        ]
        if slots <= 0:
            lines.append("当前已达到 3 只以上持仓，本轮仍列出高分观察候选，但不建议立即新开仓；先管理已有仓位。")
        elif cash < capital * 0.08:
            lines.append("当前模拟现金不足本金的 8%，本轮不建议新开仓。")
        elif not candidates:
            lines.append("今天没有达到 72 分门槛的新候选。空仓也是策略的一部分，不为凑数买入。")
        else:
            first_batch_value = min(capital * 0.10, max(0.0, cash) / len(candidates))
            for index, item in enumerate(candidates, 1):
                shares = self._round_lot(first_batch_value / item.price)
                max_shares = self._round_lot(capital * 0.20 / item.price)
                lines.extend([self._format_item(item, index)])
                if slots <= 0:
                    lines.append("观察计划：先不买；等现有持仓减到合理数量和仓位后，再重新评估是否建仓。")
                elif shares <= 0:
                    lines.append("模拟计划：一手股票已超过单只 10% 首仓预算，本轮只观察。")
                else:
                    lines.extend([
                        f"模拟首仓：{shares} 股（约 {shares * item.price:,.0f} 元，约本金 10%）",
                        f"单只上限：{max_shares} 股（约本金 20%），不一次买满",
                        f"买入条件：成交价不高于参考价的 1.5%（约 {item.price * 1.015:.2f}），高开则放弃追价",
                        f"记录命令：/paper buy {item.code} {shares} 实际成交价",
                    ])
                lines.append("")
        lines.extend([
            "规则：最多 3 只；单只上限 20%；首仓约 10%；默认硬止损 8%，再叠加趋势退出。",
            f"数据来源：{self.provider.name}",
            DISCLAIMER,
        ])
        return "\n".join(lines)

    def paper_portfolio_report(
        self,
        portfolio: dict[str, Any],
        today: date | None = None,
    ) -> str:
        positions = portfolio.get("positions") or {}
        capital = float(portfolio.get("capital") or 100_000)
        cash = float(portfolio.get("cash", capital))
        realized = float(portfolio.get("realized_pnl", 0))
        if not positions:
            return (
                f"模拟盘当前空仓｜本金 {capital:,.0f} 元｜现金 {cash:,.0f} 元\n"
                "发送 /stock picks 获取达到门槛的候选。\n"
                f"{DISCLAIMER}"
            )
        analyses, errors = self.analyze_codes(list(positions), today)
        by_code = {item.code: item for item in analyses}
        market_value = sum(
            float(position.get("quantity", 0)) * by_code[code].price
            for code, position in positions.items()
            if code in by_code
        )
        equity = cash + market_value
        lines = [
            f"模拟盘持仓检查｜估算总资产 {equity:,.0f} 元",
            f"现金 {cash:,.0f} 元｜已实现盈亏 {realized:+,.0f} 元",
            "",
        ]
        for code, position in positions.items():
            item = by_code.get(code)
            if not item:
                lines.append(f"{code}：行情分析失败，暂不生成买卖动作。")
                continue
            quantity = int(position.get("quantity", 0))
            avg_cost = float(position.get("avg_cost", 0))
            value = quantity * item.price
            pnl = (item.price - avg_cost) * quantity
            pnl_percent = (item.price / avg_cost - 1) * 100 if avg_cost else 0
            concentration = value / equity if equity > 0 else 0
            hard_stop = avg_cost * 0.92
            ma20 = float(item.metrics.get("ma20") or item.price)
            ma60 = float(item.metrics.get("ma60") or item.price)
            drawdown20 = float(item.metrics.get("drawdown20") or 0)
            action = "继续持有，暂不操作"
            action_reason = "尚未触发止损、趋势退出或集中度规则"
            sell_quantity = 0
            if item.price <= hard_stop:
                sell_quantity = quantity
                action = f"模拟卖出全部 {quantity} 股"
                action_reason = f"已触发成本价下方 8% 硬止损（{hard_stop:.2f}）"
            elif item.price < ma20 < ma60 or item.score < 40:
                sell_quantity = quantity
                action = f"模拟卖出全部 {quantity} 股"
                action_reason = "中短期趋势转弱且研究分跌破退出区"
            elif pnl_percent >= 15 and drawdown20 <= -6:
                sell_quantity = self._round_lot(quantity / 2) or quantity
                action = f"模拟卖出 {sell_quantity} 股，锁定部分利润"
                action_reason = "已有明显浮盈，同时从近 20 日高点回撤超过 6%"
            elif item.price < ma20 or item.score < 55:
                sell_quantity = self._round_lot(quantity / 2) or quantity
                action = f"模拟卖出 {sell_quantity} 股，降低一半仓位"
                action_reason = "价格跌破 20 日均线或多因子研究分转弱"
            elif concentration > 0.25:
                excess_value = value - equity * 0.20
                sell_quantity = min(quantity, self._round_lot(excess_value / item.price))
                if sell_quantity > 0:
                    action = f"模拟卖出 {sell_quantity} 股，把仓位降到约 20%"
                    action_reason = f"当前单只仓位约 {concentration:.0%}，超过 25% 集中度上限"
            lines.extend([
                f"{item.name or position.get('name') or code}（{code}）",
                f"持有 {quantity} 股｜成本 {avg_cost:.2f}｜参考价 {item.price:.2f}",
                f"浮动盈亏 {pnl:+,.0f} 元（{pnl_percent:+.1f}%）｜仓位约 {concentration:.0%}",
                f"动作：{action}",
                f"原因：{action_reason}",
                f"动态风控：硬止损 {hard_stop:.2f}｜趋势风控 {item.stop_loss:.2f}｜研究分 {item.score}",
            ])
            if sell_quantity:
                lines.append(
                    f"记录卖出后发送：/paper sell {code} {sell_quantity} 实际成交价"
                )
            lines.append("")
        if errors:
            lines.append("未完成：" + "；".join(errors))
        lines.extend([
            "模拟卖出方式：在同花顺模拟盘持仓页选股票并点卖出，按上面股数填写；"
            "优先使用限价单，不在突然高开或低开时盲目追单，成交后把实际价格告诉我。",
            f"数据来源：{self.provider.name}",
            DISCLAIMER,
        ])
        return "\n".join(lines)


def is_report_due(
    now: datetime,
    report_hour: int,
    report_minute: int,
    last_sent_date: str | None,
    is_trading_day: bool,
) -> bool:
    if not is_trading_day or last_sent_date == now.date().isoformat():
        return False
    return (now.hour, now.minute) >= (report_hour, report_minute)
