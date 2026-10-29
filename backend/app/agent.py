"""The agentic loop: Claude plans, calls tools, reads results and answers.

``AgentRunner.run`` is an async generator of UI events (plain dicts) that the API
streams to the browser as SSE. Every event carries the agent ``step`` it belongs to.

Event types:
    run_start, thinking_start, thinking_delta, text_start, text_delta, tool_start,
    tool_call, tool_result, retry, error, done
"""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from .config import PRICING, Settings
from .llm import LLMClient, LLMError, LLMEvent, LLMTurn
from .tools import TOOL_DEFINITIONS, ToolExecutor

MAX_STREAM_RETRIES = 2
STEP_LIMIT_NOTICE = (
    "You have reached the tool-call budget for this question. Do not call more tools. "
    "Write the final answer now from the results you already have, and mention anything left unverified."
)

_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def estimate_cost(model: str, usage: dict[str, int]) -> float | None:
    price = PRICING.get(model)
    if price is None:
        return None
    cost = (
        usage["input_tokens"] * price["input"]
        + usage["output_tokens"] * price["output"]
        + usage["cache_read_input_tokens"] * price["cache_read"]
        + usage["cache_creation_input_tokens"] * price["cache_write"]
    ) / 1_000_000
    return round(cost, 6)


def compact_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive text/thinking deltas so the stored replay log stays small."""
    out: list[dict[str, Any]] = []
    for ev in events:
        prev = out[-1] if out else None
        if (
            prev is not None
            and ev["type"] in ("text_delta", "thinking_delta")
            and prev["type"] == ev["type"]
            and prev.get("step") == ev.get("step")
        ):
            prev["text"] += ev["text"]
        else:
            out.append(dict(ev))
    return out


def _strip_tool_use(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = [b for b in content if b.get("type") != "tool_use"]
    if not any(b.get("type") == "text" for b in kept):
        kept.append({"type": "text", "text": "(The response was cut off before it finished.)"})
    return kept


class AgentRunner:
    def __init__(self, llm: LLMClient, executor: ToolExecutor, settings: Settings):
        self.llm = llm
        self.executor = executor
        self.settings = settings
        # Populated during ``run``.
        self.events: list[dict[str, Any]] = []
        self.new_messages: list[dict[str, Any]] = []
        self.completed = False  # True only when the history is safe to persist
        self.stop_reason: str | None = None
        self.step = 0
        self.usage = dict.fromkeys(_USAGE_KEYS, 0)
        self.model = llm.model

    def _emit(self, event: dict[str, Any]) -> dict[str, Any]:
        self.events.append(event)
        return event

    async def run(self, history: list[dict], question: str, system: str) -> AsyncIterator[dict[str, Any]]:
        started = time.perf_counter()
        yield self._emit({"type": "run_start", "step": 0, "model": self.model})
        try:
            async for event in self._loop(history, question, system):
                yield self._emit(event)
        except LLMError as exc:
            yield self._emit({"type": "error", "step": self.step, "message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - never leave the stream hanging
            yield self._emit({"type": "error", "step": self.step, "message": f"Internal error: {exc}"})
        yield self._emit({
            "type": "done",
            "step": self.step,
            "ok": self.completed,
            "stop_reason": self.stop_reason,
            "steps": self.step,
            "model": self.model,
            "usage": {**self.usage, "cost_usd": estimate_cost(self.model, self.usage)},
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        })

    async def _loop(self, history: list[dict], question: str, system: str) -> AsyncIterator[dict[str, Any]]:
        messages = list(history)
        self._append(messages, {"role": "user", "content": [{"type": "text", "text": question}]})
        force_answer = False
        while True:
            self.step += 1
            turn: LLMTurn | None = None
            async for item in self._call(self.step, system, messages, force_answer):
                if isinstance(item, LLMTurn):
                    turn = item
                else:
                    yield item
            assert turn is not None
            self.stop_reason = turn.stop_reason
            self.model = turn.model

            if turn.stop_reason == "refusal":
                yield {"type": "error", "step": self.step, "message": "The model declined to answer this request."}
                return  # not persisted: the next question starts from the previous good state
            if turn.stop_reason == "max_tokens":
                # Never run a tool call that may have been truncated.
                self._append(messages, {"role": "assistant", "content": _strip_tool_use(turn.content)})
                yield {"type": "error", "step": self.step,
                       "message": "The response hit the output token limit and was cut off."}
                break

            self._append(messages, {"role": "assistant", "content": turn.content})
            if turn.stop_reason == "pause_turn":
                continue  # the server paused a long turn; resend so it can resume
            tool_uses = [b for b in turn.content if b.get("type") == "tool_use"]
            if turn.stop_reason != "tool_use" or not tool_uses:
                break

            for b in tool_uses:
                yield {"type": "tool_call", "step": self.step, "id": b["id"], "name": b["name"],
                       "input": b.get("input")}
            blocks, result_events = await self._run_tools(self.step, tool_uses)
            for ev in result_events:
                yield ev
            if force_answer:
                # Should not happen with tool_choice=none; keep the history valid and stop.
                self._append(messages, {"role": "user", "content": blocks})
                break
            if self.step >= self.settings.max_agent_steps:
                force_answer = True
                blocks = [*blocks, {"type": "text", "text": STEP_LIMIT_NOTICE}]
            self._append(messages, {"role": "user", "content": blocks})
        self.completed = True

    def _append(self, messages: list[dict], message: dict) -> None:
        messages.append(message)
        self.new_messages.append(message)

    async def _call(
        self, step: int, system: str, messages: list[dict], force_answer: bool
    ) -> AsyncIterator[dict[str, Any] | LLMTurn]:
        """One model call with retries for streams that break mid-way."""
        for attempt in range(MAX_STREAM_RETRIES + 1):
            try:
                async for item in self.llm.stream(
                    system=system,
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                    tool_choice={"type": "none"} if force_answer else None,
                ):
                    if isinstance(item, LLMTurn):
                        for key in _USAGE_KEYS:
                            self.usage[key] += item.usage.get(key, 0)
                        yield item
                        return
                    yield self._ui_event(step, item)
            except ValueError:
                if attempt == MAX_STREAM_RETRIES:
                    raise LLMError("The response stream was interrupted repeatedly. Please try again.") from None
                yield {"type": "retry", "step": step, "attempt": attempt + 1}
                await asyncio.sleep(0.5 * (attempt + 1))
        raise LLMError("The model returned no response.")

    @staticmethod
    def _ui_event(step: int, event: LLMEvent) -> dict[str, Any]:
        if event.kind == "tool_use_start":
            return {"type": "tool_start", "step": step, "id": event.data["id"], "name": event.data["name"]}
        return {"type": event.kind, "step": step, **event.data}

    async def _run_tools(self, step: int, tool_uses: list[dict]) -> tuple[list[dict], list[dict]]:
        outcomes = await asyncio.gather(
            *(asyncio.to_thread(self.executor.execute, b["name"], b.get("input")) for b in tool_uses)
        )
        blocks: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for block, outcome in zip(tool_uses, outcomes, strict=True):
            events.append({
                "type": "tool_result",
                "step": step,
                "id": block["id"],
                "name": block["name"],
                "is_error": outcome.is_error,
                "summary": outcome.summary,
                "ui": outcome.ui,
            })
            result: dict[str, Any] = {"type": "tool_result", "tool_use_id": block["id"], "content": outcome.content}
            if outcome.is_error:
                result["is_error"] = True
            blocks.append(result)
        return blocks, events
