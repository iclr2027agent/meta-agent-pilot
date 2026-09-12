"""Thin wrapper around the Anthropic API used by the control plane.

Requires ANTHROPIC_API_KEY to be set in the environment. This module is the
only place that talks to the model — the control plane itself never assumes
a worker's text output is true; it only ever reads structured fields out of
it and then hands the *repository* (not the worker's claims) to the
deterministic verifier.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import anthropic

MODEL = "claude-sonnet-4-6"

# Approximate list pricing in USD per token, as of when this harness was
# written. These are rough and will go stale — re-check current pricing
# before trusting Csuccess numbers for publication.
PRICE_PER_INPUT_TOKEN = 3.0 / 1_000_000
PRICE_PER_OUTPUT_TOKEN = 15.0 / 1_000_000
# Prompt caching (ephemeral, 5-minute TTL): a cache write costs 1.25x the
# base input price, a cache read costs 0.1x — see docs.anthropic.com/en/
# docs/build-with-claude/prompt-caching.
PRICE_PER_CACHE_WRITE_TOKEN = PRICE_PER_INPUT_TOKEN * 1.25
PRICE_PER_CACHE_READ_TOKEN = PRICE_PER_INPUT_TOKEN * 0.1

# OpenAI gpt-5 pricing, per developers.openai.com/api/docs/pricing as of
# this writing — also rough, re-check before publication. Note the
# different cache semantics: OpenAI's cached_tokens is a SUBSET of
# prompt_tokens (already included, billed at a reduced rate), not a
# separate additive pool the way Anthropic's cache_read/write tokens are.
PRICE_PER_INPUT_TOKEN_GPT5 = 1.25 / 1_000_000
PRICE_PER_OUTPUT_TOKEN_GPT5 = 10.0 / 1_000_000
PRICE_PER_CACHED_INPUT_TOKEN_GPT5 = 0.125 / 1_000_000


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    calls: int = 0
    # "anthropic" or "openai" — determines which pricing/semantics cost_usd
    # applies. A fresh LLMUsage() defaults to "anthropic" but adopts
    # whichever provider actually did work the first time real usage
    # (calls > 0) is added to it, so accumulating a trace's usage across
    # several calls tags correctly as long as a single run doesn't mix
    # providers (it never does in this project's design).
    provider: str = "anthropic"

    def add(self, other: "LLMUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.calls += other.calls
        if other.calls > 0:
            self.provider = other.provider

    @property
    def cost_usd(self) -> float:
        if self.provider == "openai":
            uncached = max(0, self.input_tokens - self.cache_read_input_tokens)
            return (
                uncached * PRICE_PER_INPUT_TOKEN_GPT5
                + self.cache_read_input_tokens * PRICE_PER_CACHED_INPUT_TOKEN_GPT5
                + self.output_tokens * PRICE_PER_OUTPUT_TOKEN_GPT5
            )
        return (
            self.input_tokens * PRICE_PER_INPUT_TOKEN
            + self.output_tokens * PRICE_PER_OUTPUT_TOKEN
            + self.cache_creation_input_tokens * PRICE_PER_CACHE_WRITE_TOKEN
            + self.cache_read_input_tokens * PRICE_PER_CACHE_READ_TOKEN
        )


_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it before running the "
                "pilot, e.g.: export ANTHROPIC_API_KEY=sk-ant-..."
            )
        # This environment's installed Brotli build doesn't support the
        # output_buffer_limit kwarg the anthropic SDK's httpx2 transport
        # passes when decoding a brotli-encoded response, which otherwise
        # surfaces as an opaque APIConnectionError on every call. Requesting
        # uncompressed responses sidesteps it entirely.
        _client = anthropic.Anthropic(api_key=api_key, default_headers={"accept-encoding": "identity"})
    return _client


def reset_client() -> None:
    """Force the next _get_client() call to build a fresh client (fresh
    connection pool). Used when retrying after a transient network error,
    in case the pooled connection itself is left in a bad state."""
    global _client
    _client = None


_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        import openai  # local import: only needed on the cross-model path

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Export it before running a "
                "cross-model comparison, e.g.: export OPENAI_API_KEY=sk-..."
            )
        _openai_client = openai.OpenAI(api_key=api_key)
    return _openai_client


def _is_openai_model(model: str) -> bool:
    return model.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-"))


def _call_llm_openai(
    system: str, user: str, model: str, max_tokens: int, timeout: float
) -> tuple[str, LLMUsage]:
    client = _get_openai_client()
    resp = client.chat.completions.create(
        model=model,
        max_completion_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        timeout=timeout,
    )
    text = resp.choices[0].message.content or ""
    cached = 0
    details = getattr(resp.usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    usage = LLMUsage(
        input_tokens=resp.usage.prompt_tokens,
        output_tokens=resp.usage.completion_tokens,
        cache_read_input_tokens=cached,
        calls=1,
        provider="openai",
    )
    return text, usage


def call_llm(
    system: str,
    user: str,
    max_tokens: int = 4000,
    timeout: float = 90.0,
    model: str | None = None,
) -> tuple[str, LLMUsage]:
    """Call the model once. Returns (raw_text_response, usage).

    `timeout` bounds a single call so a hung request can't hang the whole
    task — the control plane needs a deterministic upper bound on every
    external call, not just on the tools it invokes itself.

    `model` optionally overrides which model/provider handles this call —
    None (the default, used by every existing caller) preserves the exact
    prior behavior (Anthropic's `MODEL`). Passing an OpenAI model id (e.g.
    "gpt-5") routes to OpenAI's chat completions API instead, for the
    cross-model comparison — same system/user prompts either way, only the
    underlying model differs. Note: OpenAI's reasoning-capable models can
    spend part of max_tokens on hidden reasoning before any visible output,
    so callers should pass a generous budget on that path.
    """
    effective_model = model or MODEL
    if _is_openai_model(effective_model):
        return _call_llm_openai(system, user, effective_model, max_tokens, timeout)

    client = _get_client()
    resp = client.messages.create(
        model=effective_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        timeout=timeout,
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    usage = LLMUsage(
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        calls=1,
    )
    return text, usage


def extract_json(text: str) -> dict:
    """Extract the model's final JSON answer from a response.

    Workers are asked to respond with ONLY a JSON object, but treat that as
    a proposal to parse defensively, not a guarantee — a worker can still
    return malformed output (or prose before its final answer), and the
    control plane must handle that as a failed delegation rather than
    crashing.

    Scans left to right; whenever a "{" starts a COMPLETE, valid JSON value
    (via raw_decode), records it as a candidate and jumps past that value's
    entire span before continuing — so a nested "{" inside an already-
    matched object (e.g. this project's executor schema, {"files": {...},
    "rationale": ..., "claims_success": ...}) is never considered as a
    separate top-level candidate; only the outermost object is. Returns the
    LAST top-level candidate found, so a model that prefaces its real
    answer with prose quoting something incidentally valid-JSON-shaped
    (e.g. a dict literal copied from real test output) doesn't get that
    preamble mistaken for the final answer. A literal "{" inside a string
    value (e.g. a "reasoning" field quoting "{some: dict}" as text) is
    handled correctly too, since it's consumed as part of its enclosing
    object's single raw_decode call, not tested as its own start position.
    """
    candidates = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            try:
                obj, end = json.JSONDecoder().raw_decode(text, i)
                candidates.append(obj)
                i = end
                continue
            except json.JSONDecodeError:
                pass
        i += 1
    if not candidates:
        raise ValueError(f"No JSON object found in worker response: {text[:200]!r}")
    return candidates[-1]
