"""Provider-agnostic LLM layer with tool calling (Anthropic Claude or OpenAI).

The agent stores conversations in one neutral format:
    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "..." | None, "tool_calls": [{"id", "name", "arguments": {...}}]}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "<json string>"}
Each provider converts that format to its own API shape on every call.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.config import Settings


class ProviderConfigError(RuntimeError):
    """Raised when a provider is selected but not configured (e.g. missing API key)."""


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class BaseProvider:
    name = "base"

    def __init__(self, model: str, price_in: float, price_out: float, temperature: float | None):
        self.model, self.price_in, self.price_out, self.temperature = model, price_in, price_out, temperature

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.price_in + output_tokens * self.price_out) / 1_000_000

    def complete(self, system: str, messages: list[dict], tools: list) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    def _create(self, create_fn, kwargs: dict):
        """Calls the SDK. If the SDK or model rejects `temperature`, retries once without it
        and stops sending it for the rest of the session."""
        try:
            return create_fn(**kwargs)
        except Exception as exc:
            if "temperature" in kwargs and "temperature" in str(exc).lower():
                kwargs.pop("temperature")
                self.temperature = None
                return create_fn(**kwargs)
            raise


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, settings: Settings):
        if not settings.anthropic_api_key:
            raise ProviderConfigError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
        super().__init__(settings.anthropic_model, settings.anthropic_price_input,
                         settings.anthropic_price_output, settings.temperature)
        import anthropic
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    @staticmethod
    def _convert(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["arguments"]})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no reply)"}]})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    def complete(self, system: str, messages: list[dict], tools: list) -> LLMResponse:
        kwargs = dict(model=self.model, max_tokens=1024, system=system, messages=self._convert(messages))
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                               for t in tools]
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = self._create(self.client.messages.create, kwargs)
        texts, calls = [], []
        for block in resp.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                calls.append({"id": block.id, "name": block.name, "arguments": dict(block.input or {})})
        return LLMResponse(text="\n".join(texts).strip() or None, tool_calls=calls,
                           input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise ProviderConfigError("OPENAI_API_KEY is not set. Add it to your .env file.")
        super().__init__(settings.openai_model, settings.openai_price_input,
                         settings.openai_price_output, settings.temperature)
        import openai
        self.client = openai.OpenAI(api_key=settings.openai_api_key)

    @staticmethod
    def _convert(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("content")}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}}
                        for tc in m["tool_calls"]
                    ]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        return out

    def complete(self, system: str, messages: list[dict], tools: list) -> LLMResponse:
        kwargs = dict(model=self.model, messages=self._convert(system, messages))
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                 "parameters": t.parameters}} for t in tools]
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = self._create(self.client.chat.completions.create, kwargs)
        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
        usage = resp.usage
        return LLMResponse(text=(msg.content or "").strip() or None, tool_calls=calls,
                           input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                           output_tokens=getattr(usage, "completion_tokens", 0) or 0)


def get_provider(settings: Settings, name: str | None = None) -> BaseProvider:
    name = (name or settings.llm_provider).lower()
    if name in ("anthropic", "claude"):
        return AnthropicProvider(settings)
    if name in ("openai", "gpt"):
        return OpenAIProvider(settings)
    raise ProviderConfigError(f"Unknown LLM_PROVIDER '{name}'. Use 'anthropic' or 'openai'.")