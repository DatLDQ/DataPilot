"""``LLMClient`` for OpenAI-compatible chat endpoints: Google Gemini and local Ollama.

The agent, tools and stored history stay Anthropic-shaped (``tool_use`` / ``tool_result``
blocks); this module translates at the edge. That keeps ``ClaudeLLM`` untouched and lets a
conversation started on one provider continue on another.

Claude-only features have no equivalent here and are simply not used: prompt caching
directives, adaptive thinking display, server-side refusal fallbacks.
"""

import asyncio
import json
import re
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import GEMINI_BASE_URL, Settings
from .llm import LLMError, LLMEvent, LLMTurn

FINISH_REASONS = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}
EFFORT_TO_REASONING = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
MAX_RATE_LIMIT_RETRIES = 3
# Gemini 3 rejects replayed function calls of the current turn without their thought signature.
# Real signatures are kept in memory; this documented placeholder covers anything older.
GEMINI_SKIP_SIGNATURE = {"google": {"thought_signature": "skip_thought_signature_validator"}}

_SUGGESTIONS_SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
    "required": ["questions"],
}


def _tool_id(raw: str | None) -> str:
    # Claude only accepts [A-Za-z0-9_-] ids, so history stays replayable there too.
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", raw or "")
    return cleaned or f"call_{uuid.uuid4().hex[:24]}"


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if b.get("type") == "text")


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {"type": "function",
         "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}}
        for t in tools
    ]


def to_openai_tool_choice(choice: dict | None) -> str | dict | None:
    if choice is None:
        return None
    kind = choice.get("type")
    if kind == "tool":
        return {"type": "function", "function": {"name": choice["name"]}}
    return {"none": "none", "any": "required"}.get(kind, "auto")


def _parse_json_object(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


class OpenAICompatLLM:
    def __init__(self, settings: Settings, provider: str | None = None):
        self.settings = settings
        self.provider = provider or settings.active_provider
        if self.provider == "gemini":
            if not settings.gemini_api_key:
                raise LLMError("GEMINI_API_KEY is not set. Add it to backend/.env and restart the backend.")
            base_url, api_key, self.model = GEMINI_BASE_URL, settings.gemini_api_key, settings.gemini_model
        else:
            base_url, api_key, self.model = settings.ollama_base_url, "ollama", settings.ollama_model
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(settings.compat_timeout_s, connect=10.0),
        )
        self._signatures: OrderedDict[str, dict] = OrderedDict()

    # -- request translation -------------------------------------------------
    def to_openai_messages(self, system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for message in messages:
            content = message["content"]
            if isinstance(content, str):
                out.append({"role": message["role"], "content": content})
            elif message["role"] == "assistant":
                out.append(self._assistant_message(content))
            else:
                out.extend(self._user_messages(content))
        return out

    def _assistant_message(self, content: list[dict]) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": _text_of(content) or None}
        calls = []
        for block in content:
            if block.get("type") != "tool_use":
                continue  # thinking blocks are Claude-specific and are not replayed
            call: dict[str, Any] = {
                "id": block["id"], "type": "function",
                "function": {"name": block["name"], "arguments": json.dumps(block.get("input") or {})},
            }
            extra = self._signatures.get(block["id"])
            if extra is None and self.provider == "gemini":
                extra = GEMINI_SKIP_SIGNATURE
            if extra is not None:
                call["extra_content"] = extra
            calls.append(call)
        if calls:
            msg["tool_calls"] = calls
        return msg

    def _user_messages(self, content: list[dict]) -> list[dict]:
        out: list[dict] = []
        texts = []
        for block in content:
            if block.get("type") == "tool_result":
                body = _text_of(block.get("content"))
                if block.get("is_error"):
                    body = f"Error: {body}"
                out.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": body})
            elif block.get("type") == "text":
                texts.append(block["text"])
        if texts:  # tool messages must directly follow the assistant turn that called them
            out.append({"role": "user", "content": "\n\n".join(texts)})
        return out

    def _remember_signature(self, call_id: str, extra: dict) -> None:
        self._signatures[call_id] = extra
        while len(self._signatures) > 500:
            self._signatures.popitem(last=False)

    def _body(self, system: str, tools: list[dict], messages: list[dict], tool_choice: dict | None) -> dict:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self.to_openai_messages(system, messages),
            "max_tokens": self.settings.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = to_openai_tools(tools)
            choice = to_openai_tool_choice(tool_choice)
            if choice is not None:
                body["tool_choice"] = choice
        if self.provider == "gemini":
            body["reasoning_effort"] = EFFORT_TO_REASONING[self.settings.effort]
        return body

    # -- transport -------------------------------------------------------------
    def _map_status(self, status: int, detail: str) -> LLMError:
        name = "Gemini" if self.provider == "gemini" else "Ollama"
        if status in (401, 403):
            return LLMError(f"{name} rejected the API key. Check GEMINI_API_KEY and restart the backend.")
        if status == 404:
            hint = f" Run `ollama pull {self.model}` first." if self.provider == "ollama" else ""
            return LLMError(f"{name} does not know the model '{self.model}'.{hint}")
        if status == 429:
            return LLMError(f"Rate limited by {name} (free-tier quota). Please wait a minute and try again.")
        if status == 400:
            return LLMError(f"{name} rejected the request: {detail[:300]}")
        return LLMError(f"{name} API error ({status}). Please try again.")

    def _connection_error(self) -> LLMError:
        if self.provider == "ollama":
            return LLMError(f"Could not reach Ollama at {self.settings.ollama_base_url}. Is `ollama serve` running?")
        return LLMError("Could not reach the Gemini API. Check your network connection.")

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        try:
            return min(float(response.headers.get("retry-after", "")), 30.0)
        except ValueError:
            return 2.0 * 2**attempt

    async def _post_stream(self, body: dict) -> AsyncIterator[dict]:
        """Yield parsed SSE chunks, backing off on free-tier rate limits before any output."""
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                async with self.client.stream("POST", "chat/completions", json=body) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode(errors="replace")
                        if response.status_code in (429, 503) and attempt < MAX_RATE_LIMIT_RETRIES:
                            await asyncio.sleep(self._retry_delay(response, attempt))
                            continue
                        raise self._map_status(response.status_code, detail)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        chunk = json.loads(data)
                        if "error" in chunk:
                            err = chunk["error"]
                            message = err.get("message", err) if isinstance(err, dict) else err
                            raise LLMError(f"Provider error: {message}")
                        yield chunk
                    return
            except httpx.ConnectError as exc:
                raise self._connection_error() from exc
            except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout) as exc:
                raise ValueError("stream interrupted") from exc  # the agent retries interrupted streams

    # -- LLMClient ---------------------------------------------------------------
    async def stream(
        self, *, system: str, tools: list[dict], messages: list[dict], tool_choice: dict | None = None
    ) -> AsyncIterator[LLMEvent | LLMTurn]:
        body = self._body(system, tools, messages, tool_choice)
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        calls: list[dict[str, Any]] = []
        by_index: dict[int, int] = {}
        finish: str | None = None
        usage: dict[str, Any] = {}
        model = self.model

        async for chunk in self._post_stream(body):
            model = chunk.get("model") or model
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    if not thinking_parts:
                        yield LLMEvent("thinking_start")
                    thinking_parts.append(reasoning)
                    yield LLMEvent("thinking_delta", {"text": reasoning})
                if delta.get("content"):
                    if not text_parts:
                        yield LLMEvent("text_start")
                    text_parts.append(delta["content"])
                    yield LLMEvent("text_delta", {"text": delta["content"]})
                for tc in delta.get("tool_calls") or []:
                    index = tc.get("index", len(calls))
                    pos = by_index.get(index)
                    fn = tc.get("function") or {}
                    # Some servers reuse index 0 for every parallel call; a new id means a new call.
                    if pos is None or (tc.get("id") and _tool_id(tc["id"]) != calls[pos]["id"]):
                        call = {"id": _tool_id(tc.get("id")), "name": fn.get("name") or "", "args": ""}
                        calls.append(call)
                        pos = by_index[index] = len(calls) - 1
                        yield LLMEvent("tool_use_start", {"id": call["id"], "name": call["name"]})
                    call = calls[pos]
                    if fn.get("name") and not call["name"]:
                        call["name"] = fn["name"]
                    call["args"] += fn.get("arguments") or ""
                    if tc.get("extra_content"):
                        call["extra"] = tc["extra_content"]
                finish = choice.get("finish_reason") or finish

        content: list[dict[str, Any]] = []
        if text_parts:
            content.append({"type": "text", "text": "".join(text_parts)})
        stop_reason = FINISH_REASONS.get(finish or "stop", "end_turn")
        for call in calls:
            try:
                args = json.loads(call["args"]) if call["args"].strip() else {}
            except json.JSONDecodeError:
                if stop_reason == "max_tokens":
                    args = {}  # the agent never runs tool calls from a truncated turn
                else:
                    raise ValueError("malformed tool arguments") from None  # retried by the agent
            if not isinstance(args, dict):
                raise ValueError("tool arguments are not an object")
            if call.get("extra"):
                self._remember_signature(call["id"], call["extra"])
            content.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": args})
        if calls and stop_reason == "end_turn":
            stop_reason = "tool_use"  # Gemini reports "stop" even when it called tools
        if not content:
            raise ValueError("empty response")

        cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
        yield LLMTurn(
            content=content,
            stop_reason=stop_reason,
            model=model.removeprefix("models/"),
            usage={
                "input_tokens": max((usage.get("prompt_tokens") or 0) - cached, 0),
                "output_tokens": usage.get("completion_tokens") or 0,
                "cache_read_input_tokens": cached,
                "cache_creation_input_tokens": 0,
            },
        )

    async def suggest_questions(self, prompt: str) -> list[str]:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "suggestions", "schema": _SUGGESTIONS_SCHEMA}},
        }
        for attempt in range(2):  # one quick retry: free tiers often answer 503/429 transiently
            try:
                response = await self.client.post("chat/completions", json=body)
            except httpx.ConnectError as exc:
                raise self._connection_error() from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"Suggestion request failed: {exc}") from exc
            if response.status_code not in (429, 503) or attempt:
                break
            await asyncio.sleep(2.0)
        if response.status_code >= 400:
            raise self._map_status(response.status_code, response.text)
        try:
            message = response.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError):
            return []
        parsed = _parse_json_object(message.get("content") or "")
        questions = parsed.get("questions") if parsed else None
        if not isinstance(questions, list):
            return []
        return [q.strip() for q in questions if isinstance(q, str) and q.strip()][:6]
