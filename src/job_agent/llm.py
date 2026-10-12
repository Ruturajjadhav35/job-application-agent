"""One small interface for every LLM provider, so switching is a .env setting.

Both providers are called over plain HTTPS with httpx (no vendor SDKs), using
the same polite client as the job sources: paced requests, retries on 429/5xx.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from job_agent.config import Settings, get_settings
from job_agent.sources.base import PoliteClient, SourceConfigError

log = logging.getLogger(__name__)

# USD per million tokens (input, output). Gemini free tier costs nothing.
# Check the provider's pricing page; these only drive the cost estimate shown to you.
PRICES = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


class LLMError(Exception):
    """The model call failed or returned something unusable."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        self.cost_usd += other.cost_usd

    def __str__(self) -> str:
        cost = f"${self.cost_usd:.3f}" if self.cost_usd else "$0 (free tier)"
        return (
            f"{self.calls} LLM calls, {self.input_tokens:,} input + "
            f"{self.output_tokens:,} output tokens, cost {cost}"
        )


@dataclass
class LLMResponse:
    text: str
    usage: Usage = field(default_factory=Usage)

    def json(self) -> dict:
        """Parse JSON even if the model wrapped it in ```json fences."""
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", self.text.strip())
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LLMError(f"Model did not return valid JSON: {e}") from None


class LLM(Protocol):
    name: str

    def complete(
        self, system: str, user: str, json_output: bool = True, temperature: float = 0.4
    ) -> LLMResponse: ...


def _secret(value) -> str | None:
    return value.get_secret_value() if value else None


class GeminiLLM:
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str, client: PoliteClient):
        self.name = f"gemini/{model}"
        self.model = model
        self._key = api_key
        self.client = client

    def complete(self, system, user, json_output=True, temperature=0.4) -> LLMResponse:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature},
        }
        if json_output:
            body["generationConfig"]["responseMimeType"] = "application/json"
        data = _post(
            self.client,
            self.URL.format(model=self.model),
            body,
            headers={"x-goog-api-key": self._key},
            provider="Gemini",
        )
        try:
            cand = data["candidates"][0]
            text = "".join(
                p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought")
            )
        except (KeyError, IndexError):
            reason = (data.get("promptFeedback") or {}).get("blockReason") or "empty reply"
            raise LLMError(f"Gemini returned no text ({reason}).") from None
        meta = data.get("usageMetadata", {})
        out_tokens = meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0)
        return LLMResponse(text, Usage(meta.get("promptTokenCount", 0), out_tokens, 1, 0.0))


class AnthropicLLM:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, client: PoliteClient):
        self.name = f"anthropic/{model}"
        self.model = model
        self._key = api_key
        self.client = client

    def complete(self, system, user, json_output=True, temperature=0.4) -> LLMResponse:
        if json_output:
            system += "\n\nReply with a single JSON object only. No prose, no code fences."
        body = {
            "model": self.model,
            "max_tokens": 4000,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01"}
        data = _post(self.client, self.URL, body, headers=headers, provider="Anthropic")
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        if not text:
            raise LLMError("Anthropic returned no text.")
        u = data.get("usage", {})
        inp, out = u.get("input_tokens", 0), u.get("output_tokens", 0)
        price_in, price_out = PRICES.get(self.model, (0.0, 0.0))
        cost = inp / 1e6 * price_in + out / 1e6 * price_out
        return LLMResponse(text, Usage(inp, out, 1, cost))


def _post(client: PoliteClient, url: str, body: dict, headers: dict, provider: str) -> dict:
    try:
        return client.post_json(url, body, headers=headers)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code in (401, 403):
            raise SourceConfigError(
                f"{provider} rejected the API key. Check your .env file."
            ) from None
        if code == 429:
            raise LLMError(
                f"{provider} rate limit reached (HTTP 429). Free-tier daily quota may "
                "be used up; try again later or tomorrow."
            ) from None
        detail = e.response.text[:300]
        raise LLMError(f"{provider} error HTTP {code}: {detail}") from None
    except httpx.TransportError as e:
        raise LLMError(f"{provider} network error: {type(e).__name__}") from None


def make_llm(settings: Settings | None = None, client: PoliteClient | None = None) -> LLM:
    s = settings or get_settings()
    client = client or PoliteClient(min_interval=s.llm_min_interval, max_requests=200, timeout=120)
    provider = s.llm_provider.lower().strip()
    if provider == "gemini":
        key = _secret(s.gemini_api_key)
        if not key:
            raise SourceConfigError("GEMINI_API_KEY is missing from your .env file.")
        return GeminiLLM(key, s.gemini_model, client)
    if provider == "anthropic":
        key = _secret(s.anthropic_api_key)
        if not key:
            raise SourceConfigError("ANTHROPIC_API_KEY is missing from your .env file.")
        return AnthropicLLM(key, s.anthropic_model, client)
    raise SourceConfigError(
        f"Unknown LLM_PROVIDER '{s.llm_provider}'. Use 'gemini' or 'anthropic'."
    )
