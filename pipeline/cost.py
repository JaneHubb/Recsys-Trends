"""LLM cost estimation (Batch prices). Actual spend is summed from the usage field of Batch results."""
from __future__ import annotations

from . import config


def tokens(text: str) -> int:
    return int(len(text or "") / config.CHARS_PER_TOKEN) + 1


def price(model: str) -> dict:
    p = config.PRICES.get(model)
    if not p:
        raise KeyError(f"config.PRICES has no price for {model}")
    return p


def estimate_usd(model: str, in_tokens: int, out_tokens: int) -> float:
    p = price(model)
    return in_tokens / 1e6 * p["in"] + out_tokens / 1e6 * p["out"]


def actual_usd(model: str, usage: dict) -> float:
    """usage: {input, output, cache_read, cache_write}. Cache reads cost 10% of input, cache writes 125%."""
    p = price(model)
    return (
        usage.get("input", 0) / 1e6 * p["in"]
        + usage.get("cache_read", 0) / 1e6 * p["in"] * 0.1
        + usage.get("cache_write", 0) / 1e6 * p["in"] * 1.25
        + usage.get("output", 0) / 1e6 * p["out"]
    )
