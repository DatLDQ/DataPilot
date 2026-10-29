import pytest
from conftest import FakeLLM, text, thinking, tool_use

from app.agent import STEP_LIMIT_NOTICE, AgentRunner, compact_events, estimate_cost
from app.llm import LLMError, sanitize_assistant_content
from app.tools import ToolExecutor

QUESTION = "Which city has the most revenue?"
SQL = "SELECT city, sum(revenue) r FROM ecommerce_sales GROUP BY 1 ORDER BY 2 DESC"


@pytest.fixture
def make_runner(manager, sample_dataset, settings):
    dataset_id, profile = sample_dataset

    def make(script):
        llm = FakeLLM(script)
        return AgentRunner(llm, ToolExecutor(manager, dataset_id, profile, settings), settings), llm

    return make


async def collect(runner, history=None):
    return [ev async for ev in runner.run(history or [], QUESTION, "system prompt")]


async def test_tool_loop_end_to_end(make_runner):
    runner, llm = make_runner([
        ([thinking("plan"), text("Let me check."), tool_use("t1", "run_sql", sql=SQL, purpose="rank")], "tool_use"),
        ([text("**TP.HCM** leads.")], "end_turn"),
    ])
    events = await collect(runner)
    types = [e["type"] for e in events]
    assert types[0] == "run_start" and types[-1] == "done"
    assert types.index("tool_start") < types.index("tool_call") < types.index("tool_result")
    result = next(e for e in events if e["type"] == "tool_result")
    assert not result["is_error"] and result["ui"]["rows"][0][0] == "TP.HCM"

    assert runner.completed
    assert [m["role"] for m in runner.new_messages] == ["user", "assistant", "user", "assistant"]
    tool_result_msg = llm.calls[1]["messages"][-1]
    assert tool_result_msg["content"][0]["tool_use_id"] == "t1"
    assert "TP.HCM" in tool_result_msg["content"][0]["content"]
    # The assistant turn is replayed exactly, thinking signature included.
    assert llm.calls[1]["messages"][1]["content"][0] == thinking("plan")

    done = events[-1]
    assert done["ok"] and done["steps"] == 2 and done["stop_reason"] == "end_turn"
    assert done["usage"]["input_tokens"] == 2000
    assert done["usage"]["cost_usd"] == pytest.approx(estimate_cost("claude-opus-5-5", runner.usage))


async def test_parallel_tool_calls_share_one_user_message(make_runner):
    runner, llm = make_runner([
        ([tool_use("a", "run_sql", sql="SELECT 1 AS x", purpose="a"),
          tool_use("b", "profile_column", table="ecommerce_sales", column="nope")], "tool_use"),
        ([text("done")], "end_turn"),
    ])
    await collect(runner)
    blocks = llm.calls[1]["messages"][-1]["content"]
    assert [b["tool_use_id"] for b in blocks] == ["a", "b"]
    assert "is_error" not in blocks[0] and blocks[1]["is_error"] is True


async def test_history_is_prepended(make_runner):
    history = [{"role": "user", "content": [text("hi")]}, {"role": "assistant", "content": [text("hello")]}]
    runner, llm = make_runner([([text("ok")], "end_turn")])
    await collect(runner, history)
    assert llm.calls[0]["messages"][:2] == history
    assert len(runner.new_messages) == 2  # only this turn's messages are new


async def test_step_limit_forces_final_answer(make_runner, settings):
    call = tool_use("t", "run_sql", sql="SELECT 1", purpose="x")
    runner, llm = make_runner([([call], "tool_use")] * settings.max_agent_steps + [([text("summary")], "end_turn")])
    events = await collect(runner)
    assert len(llm.calls) == settings.max_agent_steps + 1
    assert all(c["tool_choice"] is None for c in llm.calls[:-1])
    assert llm.calls[-1]["tool_choice"] == {"type": "none"}
    assert llm.calls[-1]["messages"][-1]["content"][-1] == {"type": "text", "text": STEP_LIMIT_NOTICE}
    assert runner.completed and events[-1]["ok"]


async def test_broken_stream_is_retried(make_runner):
    runner, llm = make_runner([ValueError("malformed event"), ([text("fine")], "end_turn")])
    events = await collect(runner)
    assert any(e["type"] == "retry" for e in events)
    assert runner.completed and len(llm.calls) == 2


async def test_api_error_is_reported_and_not_persisted(make_runner):
    runner, _ = make_runner([LLMError("Rate limited")])
    events = await collect(runner)
    assert events[-2] == {"type": "error", "step": 1, "message": "Rate limited"}
    assert events[-1]["type"] == "done" and not events[-1]["ok"]
    assert not runner.completed


async def test_refusal_is_not_persisted(make_runner):
    runner, _ = make_runner([([], "refusal")])
    events = await collect(runner)
    assert any(e["type"] == "error" for e in events)
    assert not runner.completed


async def test_truncated_tool_call_is_not_executed(make_runner):
    runner, _ = make_runner([([text("Querying"), tool_use("t", "run_sql", sql="SELECT", purpose="")], "max_tokens")])
    events = await collect(runner)
    assert not any(e["type"] == "tool_result" for e in events)
    assert runner.new_messages[-1]["content"] == [text("Querying")]
    assert runner.completed  # history stays valid: no dangling tool_use


def test_compact_events_merges_deltas():
    events = [
        {"type": "text_start", "step": 1},
        {"type": "text_delta", "step": 1, "text": "Hel"},
        {"type": "text_delta", "step": 1, "text": "lo"},
        {"type": "tool_start", "step": 1, "id": "x", "name": "run_sql"},
        {"type": "text_delta", "step": 2, "text": "A"},
        {"type": "text_delta", "step": 2, "text": "B"},
    ]
    out = compact_events(events)
    assert [e.get("text") for e in out] == [None, "Hello", None, "AB"]
    assert events[1]["text"] == "Hel"  # input is not mutated


def test_sanitize_fallback_content():
    content = [
        thinking("declined model"), text("partial"), tool_use("x", "run_sql", sql="1", purpose=""),
        {"type": "fallback", "model": "claude-opus-5-5"},
        thinking("fallback model"), text("answer"),
    ]
    assert sanitize_assistant_content(content) == [text("partial"), thinking("fallback model"), text("answer")]
    assert sanitize_assistant_content([text("x")]) == [text("x")]


async def test_missing_credentials_surface_as_llm_error(monkeypatch, settings):
    from app.llm import NO_CREDENTIALS, ClaudeLLM

    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    try:
        llm = ClaudeLLM(settings.model_copy(update={"anthropic_api_key": None}))
    except LLMError as exc:  # some SDK versions fail at construction instead
        assert str(exc) == NO_CREDENTIALS
        return
    with pytest.raises(LLMError, match="No Anthropic credentials"):
        await llm.suggest_questions("hi")
