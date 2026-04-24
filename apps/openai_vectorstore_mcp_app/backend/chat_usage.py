from __future__ import annotations

from typing import TypedDict

from agents.usage import Usage


class ModelPricing(TypedDict):
    input_per_million: float
    cached_input_per_million: float
    output_per_million: float


class ThreadUsageTotals(TypedDict):
    input_tokens: int
    output_tokens: int
    cost_usd: float


PLATFORM_COST_MULTIPLIER = 2.0

MODEL_PRICING: dict[str, ModelPricing] = {
    "gpt-4.1-mini": {
        "input_per_million": 0.40,
        "cached_input_per_million": 0.10,
        "output_per_million": 1.60,
    },
    "gpt-4.1": {
        "input_per_million": 2.00,
        "cached_input_per_million": 0.50,
        "output_per_million": 8.00,
    },
    "gpt-5.4": {
        "input_per_million": 2.50,
        "cached_input_per_million": 0.25,
        "output_per_million": 15.00,
    },
    "gpt-5.4-mini": {
        "input_per_million": 0.75,
        "cached_input_per_million": 0.075,
        "output_per_million": 4.50,
    },
    "gpt-5.4-nano": {
        "input_per_million": 0.20,
        "cached_input_per_million": 0.02,
        "output_per_million": 1.25,
    },
}


def empty_usage_totals() -> ThreadUsageTotals:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }


def calculate_usage_cost_usd(model: str | None, usage: Usage) -> float:
    if not model:
        return 0.0
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return 0.0

    cached_input_tokens = usage.input_tokens_details.cached_tokens or 0
    non_cached_input_tokens = max((usage.input_tokens or 0) - cached_input_tokens, 0)
    openai_cost = (
        (non_cached_input_tokens * pricing["input_per_million"]) / 1_000_000
        + (cached_input_tokens * pricing["cached_input_per_million"]) / 1_000_000
        + ((usage.output_tokens or 0) * pricing["output_per_million"]) / 1_000_000
    )
    return openai_cost * PLATFORM_COST_MULTIPLIER


def accumulate_usage(
    current: ThreadUsageTotals | None,
    usage: Usage,
    *,
    model: str | None,
) -> ThreadUsageTotals:
    merged = empty_usage_totals()
    if current is not None:
        merged["input_tokens"] = int(current.get("input_tokens", 0))
        merged["output_tokens"] = int(current.get("output_tokens", 0))
        merged["cost_usd"] = float(current.get("cost_usd", 0.0))

    merged["input_tokens"] += int(usage.input_tokens or 0)
    merged["output_tokens"] += int(usage.output_tokens or 0)
    merged["cost_usd"] = round(
        merged["cost_usd"] + calculate_usage_cost_usd(model, usage),
        8,
    )
    return merged
