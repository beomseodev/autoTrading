import json
import re
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def stock_name(symbol: str, bucket: int) -> str:
    query = symbol[:-3] if symbol.endswith((".KS", ".KQ")) else symbol
    try:
        return next((item["name"] for item in search_symbols(query, bucket) if item["ticker"] == symbol), symbol)
    except Exception:
        return symbol


@lru_cache(maxsize=128)
def search_symbols(query: str, bucket: int) -> list[dict]:
    request = Request(
        "https://ac.stock.naver.com/ac?" + urlencode({"q": query, "target": "stock"}),
        headers={"User-Agent": "PortfolioDashboard/1.0"},
    )
    with urlopen(request, timeout=12) as response:
        payload = json.loads(response.read(1_000_000).decode("utf-8"))
    results = []
    seen = set()
    for item in payload["items"]:
        symbol = item.get("code", "")
        market = item.get("typeCode")
        if item.get("nationCode") == "KOR" and market in ("KOSPI", "KOSDAQ"):
            if not re.fullmatch(r"[0-9]{6}", symbol):
                continue
            symbol += ".KS" if market == "KOSPI" else ".KQ"
            currency = "KRW"
        elif item.get("nationCode") == "USA" and market in ("NASDAQ", "NYSE", "AMEX"):
            symbol = symbol.replace(".", "-")
            if not re.fullmatch(r"[A-Z][A-Z0-9\-]{0,9}", symbol):
                continue
            currency = "USD"
        else:
            continue
        if symbol not in seen and isinstance(item.get("name"), str):
            results.append({"ticker": symbol, "name": item["name"], "market": market, "currency": currency})
            seen.add(symbol)
    return results[:10]
