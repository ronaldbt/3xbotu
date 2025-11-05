import os
import time
from typing import Any, Dict, Optional

import requests


_EXCHANGE_INFO_CACHE: Dict[str, Any] = {}
_EXCHANGE_INFO_CACHE_TS: Optional[float] = None
_EXCHANGE_INFO_TTL_SECONDS = 300.0


def _fetch_exchange_info() -> Dict[str, Any]:
    base_url = os.getenv("BINANCE_FAPI_BASE", "https://fapi.binance.com")
    url = f"{base_url}/fapi/v1/exchangeInfo"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def get_exchange_info(force_refresh: bool = False) -> Dict[str, Any]:
    global _EXCHANGE_INFO_CACHE, _EXCHANGE_INFO_CACHE_TS
    now = time.time()
    if (not force_refresh and _EXCHANGE_INFO_CACHE_TS is not None and
            (now - _EXCHANGE_INFO_CACHE_TS) < _EXCHANGE_INFO_TTL_SECONDS and
            _EXCHANGE_INFO_CACHE):
        return _EXCHANGE_INFO_CACHE

    data = _fetch_exchange_info()
    _EXCHANGE_INFO_CACHE = data
    _EXCHANGE_INFO_CACHE_TS = now
    return data


def get_symbol_info(symbol: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    info = get_exchange_info(force_refresh=force_refresh)
    symbols = info.get("symbols", [])
    for s in symbols:
        if s.get("symbol") == symbol:
            return s
    return None


def parse_symbol_filters(symbol: str) -> Optional[Dict[str, Any]]:
    s = get_symbol_info(symbol)
    if not s:
        return None
    result: Dict[str, Any] = {
        "symbol": s.get("symbol"),
        "status": s.get("status"),
        "marginAsset": s.get("marginAsset"),
        "pricePrecision": s.get("pricePrecision"),
        "quantityPrecision": s.get("quantityPrecision"),
        "baseAssetPrecision": s.get("baseAssetPrecision"),
        "quotePrecision": s.get("quotePrecision"),
    }
    for f in s.get("filters", []):
        ftype = f.get("filterType")
        if ftype == "PRICE_FILTER":
            result.update({
                "tickSize": f.get("tickSize"),
                "minPrice": f.get("minPrice"),
                "maxPrice": f.get("maxPrice"),
            })
        elif ftype == "LOT_SIZE":
            result.update({
                "stepSize": f.get("stepSize"),
                "minQty": f.get("minQty"),
                "maxQty": f.get("maxQty"),
            })
        elif ftype == "MIN_NOTIONAL":
            result.update({
                "notional": f.get("notional"),
            })
    return result


