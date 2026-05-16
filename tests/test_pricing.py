from backend.models import TokenUsage
from backend.pricing import annotate_usage, estimate_cost

PRICING = {
    "claude-opus-4-7": {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
        "cache_write": 18.75,
    },
}


def test_estimate_cost_known_model():
    u = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost("claude-opus-4-7", u, PRICING) == 90.0


def test_estimate_cost_includes_cache():
    u = TokenUsage(
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
    )
    assert estimate_cost("claude-opus-4-7", u, PRICING) == 1.5 + 18.75


def test_estimate_cost_unknown_model():
    assert estimate_cost("mystery-9", TokenUsage(input_tokens=100), PRICING) is None


def test_estimate_cost_none_model():
    assert estimate_cost(None, TokenUsage(input_tokens=100), PRICING) is None


def test_annotate_usage_sets_cost():
    u = TokenUsage(input_tokens=1_000_000)
    annotate_usage("claude-opus-4-7", u, PRICING)
    assert u.cost_estimate_usd == 15.0


def test_estimate_cost_handles_non_string_model():
    """Guard for #31: a non-string `model` (list/dict from a botched config
    parse) used to crash on dict.get with TypeError("unhashable type").
    Should now early-return None instead of taking down the scheduler tick."""
    u = TokenUsage(input_tokens=100)
    assert estimate_cost(["weird"], u, PRICING) is None
    assert estimate_cost({"oops": 1}, u, PRICING) is None
    assert estimate_cost(123, u, PRICING) is None
    assert estimate_cost("", u, PRICING) is None
