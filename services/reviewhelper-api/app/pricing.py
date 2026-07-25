"""Per-model pricing for cost estimation. USD per 1M tokens.

Ported from qreviews (qreviews/pricing.py). Used to turn the token usage recorded
on each review request into an estimated dollar cost for the dashboard.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float


# Anthropic published rates (USD / 1M tokens) as of late 2025/early 2026.
# Update this table when rates change.
PRICES: dict[str, ModelPrice] = {
    # Claude Opus 4.x
    "claude-opus-4-8": ModelPrice(15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-7": ModelPrice(15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-6": ModelPrice(15.00, 75.00, 1.50, 18.75),
    # Claude Sonnet 4.x
    "claude-sonnet-4-6": ModelPrice(3.00, 15.00, 0.30, 3.75),
    "claude-sonnet-4-5": ModelPrice(3.00, 15.00, 0.30, 3.75),
    # Claude Haiku 4.x
    "claude-haiku-4-5": ModelPrice(1.00, 5.00, 0.10, 1.25),
    "claude-haiku-4-5-20251001": ModelPrice(1.00, 5.00, 0.10, 1.25),
}

# Generic fallback if a model ID isn't in the table — assume Sonnet-class.
FALLBACK_PRICE = ModelPrice(3.00, 15.00, 0.30, 3.75)


def price_for(model: str) -> ModelPrice:
    if not model:
        return FALLBACK_PRICE
    if model in PRICES:
        return PRICES[model]
    # Try a prefix match (e.g. snapshot IDs).
    for key, price in PRICES.items():
        if model.startswith(key):
            return price
    return FALLBACK_PRICE


def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
) -> float:
    p = price_for(model)
    return (
        input_tokens * p.input_per_mtok
        + output_tokens * p.output_per_mtok
        + cache_read * p.cache_read_per_mtok
        + cache_write * p.cache_write_per_mtok
    ) / 1_000_000.0
