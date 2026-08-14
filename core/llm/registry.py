"""Model registry.

Maps a model id to the provider that serves it, its price, and the knobs it
accepts. Providers differ in how reasoning depth is expressed, so the registry
normalises our canonical effort scale (low..max) onto each vendor's parameter.

Prices are *defaults baked into the image*. They go stale whenever a vendor
changes rates, so `core/llm/pricing.py` overlays an operator-editable
`model_prices` table on top of this table at runtime. Never bill from this file
alone — see `ModelSpec.price_verified_at`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class Provider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    XAI = "xai"
    GOOGLE = "google"
    OPENROUTER = "openrouter"


class EffortStyle(StrEnum):
    """How a model expresses "think harder"."""

    ANTHROPIC = "anthropic"  # output_config.effort + thinking: adaptive
    OPENAI_REASONING = "openai_reasoning"  # reasoning.effort
    GOOGLE_THINKING = "google_thinking"  # thinking_config.thinking_budget
    NONE = "none"  # model has no reasoning knob


# Canonical effort scale used across the product.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# Our scale -> OpenAI's `reasoning.effort`, which has no xhigh/max.
OPENAI_EFFORT = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}

# Our scale -> a Gemini thinking-token budget. -1 means "let the model decide".
GOOGLE_THINKING_BUDGET = {
    "low": 1024,
    "medium": 8192,
    "high": 24576,
    "xhigh": -1,
    "max": -1,
}


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: Provider
    display_name: str
    input_per_mtok: float
    output_per_mtok: float
    max_output_tokens: int
    context_window: int
    effort_style: EffortStyle = EffortStyle.NONE
    # Cached-input rate, when the vendor bills reads cheaper than fresh input.
    cached_input_per_mtok: float | None = None
    supports_prompt_cache: bool = False
    supports_json_mode: bool = True
    # Date the price was last checked against the vendor's public pricing page.
    price_verified_at: date = date(2026, 8, 14)
    aliases: tuple[str, ...] = field(default_factory=tuple)


def _spec(**kwargs) -> ModelSpec:
    return ModelSpec(**kwargs)


_MODELS: list[ModelSpec] = [
    # --- Anthropic ---
    _spec(
        id="claude-opus-5",
        provider=Provider.ANTHROPIC,
        display_name="Claude Opus 5",
        input_per_mtok=5.00,
        output_per_mtok=25.00,
        cached_input_per_mtok=0.50,
        max_output_tokens=128_000,
        context_window=1_000_000,
        effort_style=EffortStyle.ANTHROPIC,
        supports_prompt_cache=True,
    ),
    _spec(
        id="claude-sonnet-5",
        provider=Provider.ANTHROPIC,
        display_name="Claude Sonnet 5",
        # Introductory pricing ($2/$10) runs through 2026-08-31; these are the
        # standard rates, so we over-estimate rather than under-bill.
        input_per_mtok=3.00,
        output_per_mtok=15.00,
        cached_input_per_mtok=0.30,
        max_output_tokens=128_000,
        context_window=1_000_000,
        effort_style=EffortStyle.ANTHROPIC,
        supports_prompt_cache=True,
    ),
    _spec(
        id="claude-haiku-4-5",
        provider=Provider.ANTHROPIC,
        display_name="Claude Haiku 4.5",
        input_per_mtok=1.00,
        output_per_mtok=5.00,
        cached_input_per_mtok=0.10,
        max_output_tokens=64_000,
        context_window=200_000,
        effort_style=EffortStyle.NONE,  # no adaptive thinking / effort knob
        supports_prompt_cache=True,
    ),
    _spec(
        id="claude-opus-4-8",
        provider=Provider.ANTHROPIC,
        display_name="Claude Opus 4.8",
        input_per_mtok=5.00,
        output_per_mtok=25.00,
        cached_input_per_mtok=0.50,
        max_output_tokens=128_000,
        context_window=1_000_000,
        effort_style=EffortStyle.ANTHROPIC,
        supports_prompt_cache=True,
    ),
    # --- OpenAI ---
    _spec(
        id="gpt-5",
        provider=Provider.OPENAI,
        display_name="GPT-5",
        input_per_mtok=1.25,
        output_per_mtok=10.00,
        cached_input_per_mtok=0.125,
        max_output_tokens=128_000,
        context_window=400_000,
        effort_style=EffortStyle.OPENAI_REASONING,
        supports_prompt_cache=True,
    ),
    _spec(
        id="gpt-5-mini",
        provider=Provider.OPENAI,
        display_name="GPT-5 mini",
        input_per_mtok=0.25,
        output_per_mtok=2.00,
        cached_input_per_mtok=0.025,
        max_output_tokens=128_000,
        context_window=400_000,
        effort_style=EffortStyle.OPENAI_REASONING,
        supports_prompt_cache=True,
    ),
    # --- xAI (OpenAI-compatible wire format) ---
    _spec(
        id="grok-4",
        provider=Provider.XAI,
        display_name="Grok 4",
        input_per_mtok=3.00,
        output_per_mtok=15.00,
        max_output_tokens=64_000,
        context_window=256_000,
        effort_style=EffortStyle.NONE,  # grok-4 reasons always; no effort knob
    ),
    _spec(
        id="grok-4-fast",
        provider=Provider.XAI,
        display_name="Grok 4 Fast",
        input_per_mtok=0.20,
        output_per_mtok=0.50,
        max_output_tokens=32_000,
        context_window=2_000_000,
        effort_style=EffortStyle.NONE,
    ),
    _spec(
        id="grok-3-mini",
        provider=Provider.XAI,
        display_name="Grok 3 mini",
        input_per_mtok=0.30,
        output_per_mtok=0.50,
        max_output_tokens=32_000,
        context_window=131_072,
        effort_style=EffortStyle.OPENAI_REASONING,
    ),
    # --- Google ---
    _spec(
        id="gemini-2.5-pro",
        provider=Provider.GOOGLE,
        display_name="Gemini 2.5 Pro",
        input_per_mtok=1.25,
        output_per_mtok=10.00,
        cached_input_per_mtok=0.31,
        max_output_tokens=65_536,
        context_window=1_048_576,
        effort_style=EffortStyle.GOOGLE_THINKING,
        supports_prompt_cache=True,
    ),
    _spec(
        id="gemini-2.5-flash",
        provider=Provider.GOOGLE,
        display_name="Gemini 2.5 Flash",
        input_per_mtok=0.30,
        output_per_mtok=2.50,
        cached_input_per_mtok=0.075,
        max_output_tokens=65_536,
        context_window=1_048_576,
        effort_style=EffortStyle.GOOGLE_THINKING,
        supports_prompt_cache=True,
    ),
]

MODELS: dict[str, ModelSpec] = {spec.id: spec for spec in _MODELS}
for _spec_obj in _MODELS:
    for _alias in _spec_obj.aliases:
        MODELS[_alias] = _spec_obj


class UnknownModelError(KeyError):
    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(
            f"model {model!r} is not in the registry; register it before using it "
            f"so cost accounting and capability routing stay correct"
        )


def get_spec(model: str) -> ModelSpec:
    try:
        return MODELS[model]
    except KeyError:
        raise UnknownModelError(model) from None


def provider_for(model: str) -> Provider:
    return get_spec(model).provider


def list_models(provider: Provider | None = None) -> list[ModelSpec]:
    seen: dict[str, ModelSpec] = {}
    for spec in MODELS.values():
        if provider is not None and spec.provider != provider:
            continue
        seen[spec.id] = spec
    return sorted(seen.values(), key=lambda s: (s.provider, s.id))


def normalise_effort(effort: str) -> str:
    return effort if effort in EFFORT_LEVELS else "medium"
