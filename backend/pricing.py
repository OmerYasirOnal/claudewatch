from __future__ import annotations

from backend.models import TokenUsage


def estimate_cost(
    model: str | None,
    usage: TokenUsage,
    pricing: dict[str, dict[str, float]],
) -> float | None:
    if not model:
        return None
    rates = pricing.get(model)
    if not rates:
        return None
    return (
        usage.input_tokens * rates.get("input", 0)
        + usage.output_tokens * rates.get("output", 0)
        + usage.cache_read_input_tokens * rates.get("cache_read", 0)
        + usage.cache_creation_input_tokens * rates.get("cache_write", 0)
    ) / 1_000_000


def annotate_usage(
    model: str | None,
    usage: TokenUsage,
    pricing: dict[str, dict[str, float]],
) -> TokenUsage:
    usage.cost_estimate_usd = estimate_cost(model, usage, pricing)
    return usage
