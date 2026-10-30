"""Independent formula checker, NOT a trading engine or execution simulator.

Frozen source: AlexWan/OsEngine 09b0eefc7ac6cf5a73e461cc3a81ded9c34cd54b.
Only sequential, completed, ordinary candles of one dated contract are accepted.
Does not send requests, generate positions, emulate fills, or report profit.

Usage: python independent_indicators.py INPUT.json OUTPUT.jsonl
Input: JSON list of {begin, open, high, low, close, volume, end?}.
Output: every input candle, readiness and source-code raw conditions. Decimal
values are strings. begin+60 minutes is the event boundary; raw end is retained
as last-trade metadata only. Gaps remain gaps; state is not silently reset.

Important source peculiarities preserved:
* A uses Median and disables wicks; the robot waits for 60 bars.
* ATR includes zero TR at index 0 in its initial mean. Its recursive internal
  value is rounded to nine places; the initial mean is not internally rounded.
* BB21 first writes at index 22, uses population divisor 21, and rounds to 6 dp.
* RSI14 first writes at index 35. Earlier calls cannot seed its moving average.
  Each later call rebuilds 35 changes, seeds the first 15 changes / 14, then
  smooths 20 changes with alpha 1/14. Either zero side produces RSI 100.
* B admits Count >= 21, without checking that RSI is ready. Early RSI=0 can
  create a Buy condition; this verifier deliberately preserves that defect.

Python Decimal uses high precision with ROUND_HALF_EVEN. C# decimal arithmetic
has a 96-bit mantissa; the insignificant pre-rounding tail is not asserted to
be bit-identical. Double-to-decimal conversions in BB model documented .NET
15-significant-digit conversion. Compare native displayed BB to 1e-6, ST/ATR
to 1e-9 and RSI to 0.01, while requiring EXACT raw condition/direction matches.
Any near-threshold native mismatch must be investigated, never rounded away.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import math
from pathlib import Path

D = Decimal
COMMIT = "09b0eefc7ac6cf5a73e461cc3a81ded9c34cd54b"
SOURCE_BLOBS = {
    "ATR.cs": "aa12405cb7d790fa8e95e4759fe62df32cc62d58",
    "SuperTrend_indicator.cs": "ca41cb522e8bc6789d49afbfb8e6210e9524e918",
    "Bollinger.cs": "4019915bd6ebb24124ec0486828362a2ab3e5312",
    "RSI.cs": "ef815324af93c8dc54beca2f0c45c5232f722ff3",
    "Sma.cs": "d3cc4e1acf1e0064cf1f2313e991cde3d18c4d99",
    "ScriptSpells.cs": "5143b15af4ac352263fe27fef061e44827548e26",
}


def rounded(value: D, places: int) -> D:
    return value.quantize(D(10) ** -places, rounding=ROUND_HALF_EVEN)


def from_double(value: float) -> D:
    # Convert.ToDecimal(double) limits the result to 15 significant digits.
    return D(format(value, ".15g"))


def atr_series(candles: list[dict], length: int) -> list[D]:
    internal = []
    true_ranges = []
    for index, candle in enumerate(candles):
        if index == 0:
            tr = D(0)
        else:
            previous_close = candles[index - 1]["close"]
            tr = max(abs(candle["high"] - candle["low"]),
                     abs(candle["high"] - previous_close),
                     abs(candle["low"] - previous_close))
        true_ranges.append(tr)
        if index < length - 1:
            value = D(0)
        elif index == length - 1:
            value = sum(true_ranges, D(0)) / length
        else:
            value = rounded((internal[-1] * (length - 1) + rounded(tr, 9)) / length, 9)
        internal.append(value)
    return [rounded(value, 9) for value in internal]


def supertrend(candles: list[dict], length: int) -> list[dict]:
    atr = atr_series(candles, length)
    previous_lower = previous_upper = D(0)
    direction = 0
    rows = []
    for index, candle in enumerate(candles):
        if index < length:
            rows.append({"atr": atr[index], "lower": D(0), "upper": D(0),
                         "center": D(0), "direction": 0, "ready": False})
            continue
        midpoint = (candle["high"] + candle["low"]) / 2
        lower, upper = midpoint - atr[index], midpoint + atr[index]
        doji = candle["open"] == candle["high"] == candle["low"] == candle["close"]
        if lower <= 0 or doji:
            lower = previous_lower
        elif candles[index - 1]["close"] > previous_lower:
            lower = max(lower, previous_lower)
        if upper <= 0 or doji:
            upper = previous_upper
        elif candles[index - 1]["close"] < previous_upper:
            upper = min(upper, previous_upper)
        if candle["close"] > previous_upper:
            direction = 1
        elif candle["close"] < previous_lower:
            direction = -1
        center = lower if direction == 1 else upper
        rows.append({"atr": atr[index], "lower": lower, "upper": upper,
                     "center": center, "direction": direction, "ready": True})
        previous_lower, previous_upper = lower, upper
    return rows


def native_rsi14(candles: list[dict], index: int) -> D:
    if index < 35:
        return D(0)
    changes = [candles[j]["close"] - candles[j - 1]["close"]
               for j in range(index - 34, index + 1)]
    gains = [max(value, D(0)) for value in changes]
    losses = [max(-value, D(0)) for value in changes]
    # Source MovingAverageHard includes valueInd=14 through 0: fifteen terms.
    average_gain = sum(gains[:15], D(0)) / 14
    average_loss = sum(losses[:15], D(0)) / 14
    for j in range(15, 35):
        average_gain += (gains[j] - average_gain) / 14
        average_loss += (losses[j] - average_loss) / 14
    if average_gain == 0 or average_loss == 0:
        return D(100)
    return rounded(100 * (1 - average_loss / (average_loss + average_gain)), 2)


def bollinger21(candles: list[dict], index: int) -> dict:
    if index <= 21:
        return {"upper": D(0), "lower": D(0), "center": D(0), "ready": False}
    sample = candles[index - 20:index + 1]
    mean = sum((candle["close"] for candle in sample), D(0)) / 21
    # Preserve the source's decimal -> double -> Pow -> decimal -> double path.
    squared = [from_double(math.pow(float(candle["close"] - mean), 2))
               for candle in sample]
    sum_double = 0.0
    for value in squared:
        sum_double += float(value)
    stdev = from_double(math.sqrt(sum_double / 21))
    return {"upper": rounded(mean + stdev, 6), "lower": rounded(mean - stdev, 6),
            "center": mean, "ready": True}


def calculate(candles: list[dict]) -> list[dict]:
    if not candles:
        raise ValueError("An empty series is not a reproduction test")
    previous = None
    for candle in candles:
        begin = datetime.fromisoformat(candle["begin"])
        if previous is not None and begin <= previous:
            raise ValueError("Candles must be strictly increasing, with no duplicates")
        previous = begin
        for key in ("open", "high", "low", "close", "volume"):
            candle[key] = D(str(candle[key]))
            if not candle[key].is_finite():
                raise ValueError("Non-finite input")
        if not (candle["low"] <= min(candle["open"], candle["close"])
                <= max(candle["open"], candle["close"]) <= candle["high"]):
            raise ValueError("Invalid OHLC")
        if candle["volume"] < 0:
            raise ValueError("Negative volume")

    with localcontext() as ctx:
        ctx.prec = 50
        ctx.rounding = ROUND_HALF_EVEN
        fast = supertrend(candles, 10)
        slow = supertrend(candles, 50)
        bb = [bollinger21(candles, i) for i in range(len(candles))]
        rows = []
        for index, candle in enumerate(candles):
            close = candle["close"]
            a_ready = index >= 59
            a_buy = a_ready and close > fast[index]["center"] > slow[index]["center"]
            a_sell = a_ready and close < fast[index]["center"] < slow[index]["center"]
            rsi = native_rsi14(candles, index)
            b_gate = index >= 20
            b_buy = (b_gate and candles[index - 1]["low"] < bb[index - 1]["lower"]
                     and candle["low"] > bb[index]["lower"] and rsi < 30)
            b_sell = (b_gate and candles[index - 1]["high"] > bb[index - 1]["upper"]
                      and candle["high"] < bb[index]["upper"] and rsi > 70)
            rows.append({
                "index": index, "begin": candle["begin"],
                "h1_close_msk": (datetime.fromisoformat(candle["begin"]) + timedelta(hours=1)).isoformat(),
                "raw_end_last_trade": candle.get("end"),
                "ohlcv": {k: candle[k] for k in ("open", "high", "low", "close", "volume")},
                "fast": fast[index], "slow": slow[index], "bollinger": bb[index],
                "rsi": rsi, "rsi_ready": index >= 35,
                "A_robot_warmup_passed": a_ready, "A_raw_buy": a_buy, "A_raw_sell": a_sell,
                "A_close_long_condition": a_ready and fast[index]["center"] < slow[index]["center"],
                "A_close_short_condition": a_ready and fast[index]["center"] > slow[index]["center"],
                "B_robot_warmup_passed": b_gate, "B_raw_buy": b_buy, "B_raw_sell": b_sell,
                "B_raw_signal_with_unready_RSI": (b_buy or b_sell) and index < 35,
                "B_proposed_trailing_long": candle["low"] * D("0.99"),
                "B_proposed_trailing_short": candle["high"] * D("1.01"),
                "conditions_are_not_orders": True,
            })
        return rows


def json_default(value):
    if isinstance(value, D):
        return str(value)
    raise TypeError(type(value).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    candles = json.loads(raw, parse_float=D, parse_int=D)
    if not isinstance(candles, list):
        raise ValueError("Input must be a JSON list")
    rows = calculate(candles)
    if args.output.exists():
        raise FileExistsError("Preserve earlier attempts: choose a new output filename")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, default=json_default) + "\n")
    metadata = {
        "status": "FORMULA_CHECK_ONLY_NOT_NATIVE_ENGINE_NOT_BACKTEST",
        "source_commit": COMMIT, "source_blobs": SOURCE_BLOBS,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "bars": len(rows), "first_begin": rows[0]["begin"], "last_begin": rows[-1]["begin"],
        "A_raw_condition_bars": sum(r["A_raw_buy"] or r["A_raw_sell"] for r in rows),
        "B_raw_condition_bars": sum(r["B_raw_buy"] or r["B_raw_sell"] for r in rows),
        "B_unready_RSI_conditions": [r["index"] for r in rows if r["B_raw_signal_with_unready_RSI"]],
        "numerical_comparison_tolerances": {"ATR_ST": "0.000000001", "BB": "0.000001", "RSI": "0.01"},
        "direction_or_raw_condition_mismatch_tolerance": 0,
        "timezone": "Europe/Moscow UTC+03:00", "wicks": False,
        "regime_assumption": "On", "non_trade_periods_assumption": "All observed bars allowed",
        "notes": ["No position state; repeated conditions are NOT repeated entries.",
                  "Proposed trailing levels are NOT proof of installed stops.",
                  "Floating decimal tails require comparison with native output."],
    }
    meta_path = args.output.with_suffix(args.output.suffix + ".metadata.json")
    with meta_path.open("x", encoding="utf-8") as stream:
        json.dump(metadata, stream, ensure_ascii=False, indent=2)
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
