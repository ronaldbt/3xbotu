import json
import sys
from typing import List

sys.path.append("/home/vlad/botu-3x")

from backend.app.utils.binance_futures_info import parse_symbol_filters  # noqa: E402


def main(symbols: List[str]) -> None:
    for symbol in symbols:
        info = parse_symbol_filters(symbol)
        if not info:
            print(f"❌ No se encontró información para {symbol}")
            continue
        print(f"\n=== {symbol} ===")
        print(json.dumps(info, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    default_symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "PAXGUSDT"]
    args = sys.argv[1:]
    symbols = args if args else default_symbols
    main(symbols)


