"""LLM 비용 추정 (Batch 단가). 실제 사용량은 Batch 결과의 usage 로 집계한다."""
from __future__ import annotations

from . import config


def tokens(text: str) -> int:
    return int(len(text or "") / config.CHARS_PER_TOKEN) + 1


def price(model: str) -> dict:
    p = config.PRICES.get(model)
    if not p:
        raise KeyError(f"config.PRICES 에 {model} 단가가 없습니다")
    return p


def estimate_usd(model: str, in_tokens: int, out_tokens: int) -> float:
    p = price(model)
    return in_tokens / 1e6 * p["in"] + out_tokens / 1e6 * p["out"]


def actual_usd(model: str, usage: dict) -> float:
    """usage: {input, output, cache_read, cache_write}. 캐시 읽기는 입력의 10%, 캐시 쓰기는 125%로 계산"""
    p = price(model)
    return (
        usage.get("input", 0) / 1e6 * p["in"]
        + usage.get("cache_read", 0) / 1e6 * p["in"] * 0.1
        + usage.get("cache_write", 0) / 1e6 * p["in"] * 1.25
        + usage.get("output", 0) / 1e6 * p["out"]
    )
