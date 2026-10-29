import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.demo import DemoLLM
from app.llm import ClaudeLLM, LLMError, LLMTurn
from app.main import create_app, make_llm
from app.openai_compat import GEMINI_SKIP_SIGNATURE, OpenAICompatLLM
from app.tools import TOOL_DEFINITIONS

SQL = "SELECT channel, sum(revenue) AS revenue FROM ecommerce_sales GROUP BY 1 ORDER BY 2 DESC"


def sse(*chunks: dict) -> str:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def delta(**d) -> dict:
    return {"model": "gemini-test", "choices": [{"index": 0, "delta": d, "finish_reason": None}]}


def finish(reason: str, prompt: int = 120, completion: int = 30, cached: int = 20) -> dict:
    return {"model": "gemini-test", "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                      "prompt_tokens_details": {"cached_tokens": cached}}}


def tool_text_reply(text: str) -> str:
    return sse(delta(content=text), finish("stop"))


class Server:
    """Scripted OpenAI-compatible endpoint behind httpx.MockTransport."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        item = self.responses.pop(0)
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, text=item, headers={"content-type": "text/event-stream"})


def make(settings: Settings, server: Server, provider: str = "gemini") -> OpenAICompatLLM:
    llm = OpenAICompatLLM(settings, provider)
    llm.client = httpx.AsyncClient(transport=httpx.MockTransport(server.handler), base_url="http://llm.test/v1/")
    return llm


@pytest.fixture
def gemini_settings(settings) -> Settings:
    return settings.model_copy(update={"anthropic_api_key": None, "gemini_api_key": "g-key"})


async def collect(llm, **kwargs):
    events, turn = [], None
    async for item in llm.stream(**kwargs):
        if isinstance(item, LLMTurn):
            turn = item
        else:
            events.append(item)
    return events, turn


def test_provider_selection(settings, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Claude key present: exactly the pre-existing behaviour, whatever else is set.
    claude = settings.model_copy(update={"gemini_api_key": "g-key"})
    assert claude.active_provider == "claude" and not claude.use_demo and isinstance(make_llm(claude), ClaudeLLM)
    gemini = settings.model_copy(update={"anthropic_api_key": None, "gemini_api_key": "g-key"})
    assert gemini.active_provider == "gemini" and gemini.active_model == "gemini-3.5-flash"
    assert isinstance(make_llm(gemini), OpenAICompatLLM)
    ollama = settings.model_copy(update={"anthropic_api_key": None, "provider": "ollama"})
    assert ollama.api_key_configured and make_llm(ollama).model == "qwen3:8b"
    nothing = settings.model_copy(update={"anthropic_api_key": None})
    assert nothing.use_demo and isinstance(make_llm(nothing), DemoLLM)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        OpenAICompatLLM(settings.model_copy(update={"provider": "gemini"}))


def test_health_reports_provider(gemini_settings):
    with TestClient(create_app(gemini_settings)) as client:
        body = client.get("/api/health").json()
    assert body["provider"] == "gemini" and body["model"] == "gemini-3.5-flash" and body["api_key_configured"]


def test_history_translation(gemini_settings):
    llm = OpenAICompatLLM(gemini_settings)
    history = [
        {"role": "user", "content": [{"type": "text", "text": "Revenue by channel?"}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "plan", "signature": "s"},
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "t1", "name": "run_sql", "input": {"sql": "SELECT 1"}},
            {"type": "tool_use", "id": "t2", "name": "run_sql", "input": {"sql": "SELECT x"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "1 row"},
            {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "no column x"}],
             "is_error": True},
            {"type": "text", "text": "Answer now."},
        ]},
    ]
    out = llm.to_openai_messages("SYS", history)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "tool", "user"]
    assistant = out[2]
    assert assistant["content"] == "Checking."
    assert [c["function"]["name"] for c in assistant["tool_calls"]] == ["run_sql", "run_sql"]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"sql": "SELECT 1"}
    assert assistant["tool_calls"][0]["extra_content"] == GEMINI_SKIP_SIGNATURE
    assert out[4] == {"role": "tool", "tool_call_id": "t2", "content": "Error: no column x"}
    assert out[5] == {"role": "user", "content": "Answer now."}
    assert "extra_content" not in OpenAICompatLLM(gemini_settings, "ollama")._assistant_message(
        history[1]["content"])["tool_calls"][0]


async def test_stream_text_and_tool_calls(gemini_settings):
    signature = {"google": {"thought_signature": "abc"}}
    server = Server([sse(
        delta(reasoning_content="thinking..."),
        delta(content="Let me "), delta(content="check."),
        delta(tool_calls=[{"index": 0, "id": "fc/1", "type": "function", "extra_content": signature,
                           "function": {"name": "run_sql", "arguments": '{"sql": "SEL'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'ECT 1"}'}}]),
        # Same index, new id: a second parallel call.
        delta(tool_calls=[{"index": 0, "id": "fc2", "function": {"name": "profile_column",
                                                                 "arguments": '{"column": "city"}'}}]),
        finish("stop"),
    )])
    llm = make(gemini_settings, server)
    events, turn = await collect(llm, system="SYS", tools=TOOL_DEFINITIONS,
                                 messages=[{"role": "user", "content": "hi"}], tool_choice={"type": "none"})
    kinds = [e.kind for e in events]
    assert kinds == ["thinking_start", "thinking_delta", "text_start", "text_delta", "text_delta",
                     "tool_use_start", "tool_use_start"]
    assert turn.stop_reason == "tool_use"  # Gemini says "stop" even when it calls tools
    assert turn.content == [
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "fc_1", "name": "run_sql", "input": {"sql": "SELECT 1"}},
        {"type": "tool_use", "id": "fc2", "name": "profile_column", "input": {"column": "city"}},
    ]
    assert turn.usage == {"input_tokens": 100, "output_tokens": 30, "cache_read_input_tokens": 20,
                          "cache_creation_input_tokens": 0}

    request = server.requests[0]
    assert request["tool_choice"] == "none" and request["reasoning_effort"] == "medium"
    assert {t["function"]["name"] for t in request["tools"]} == {"run_sql", "profile_column", "create_chart"}
    # The real thought signature goes back with the replayed call.
    replay = llm._assistant_message(turn.content)
    assert replay["tool_calls"][0]["extra_content"] == signature


async def test_rate_limit_backoff_then_success(gemini_settings):
    server = Server([httpx.Response(429, headers={"retry-after": "0"}, text="quota"), tool_text_reply("Done.")])
    _, turn = await collect(make(gemini_settings, server), system="S", tools=[], messages=[])
    assert turn.content == [{"type": "text", "text": "Done."}] and len(server.requests) == 2


async def test_errors_are_user_safe(gemini_settings):
    server = Server([httpx.Response(404, text="not found")])
    with pytest.raises(LLMError, match="ollama pull qwen3:8b"):
        await collect(make(gemini_settings, server, "ollama"), system="S", tools=[], messages=[])
    server = Server([sse(delta(tool_calls=[{"index": 0, "id": "a", "function": {"name": "run_sql",
                                                                                 "arguments": "{bad"}}]),
                         finish("tool_calls"))])
    with pytest.raises(ValueError):  # the agent retries interrupted / malformed streams
        await collect(make(gemini_settings, server), system="S", tools=[], messages=[])


async def test_suggest_questions(gemini_settings):
    content = '```json\n{"questions": ["Top city?", " ", "Monthly trend?"]}\n```'
    server = Server([httpx.Response(503), httpx.Response(200, json={"choices": [{"message": {"content": content}}]})])
    llm = make(gemini_settings, server)
    assert await llm.suggest_questions("prompt") == ["Top city?", "Monthly trend?"]
    assert server.requests[0]["response_format"]["type"] == "json_schema"


def test_full_agent_turn_over_api(gemini_settings):
    """The real agent loop, tools and persistence driven by an OpenAI-compatible provider."""
    args = json.dumps({"title": "Revenue by channel", "chart_type": "bar", "sql": SQL, "x": "channel",
                       "y": ["revenue"]})
    server = Server([
        sse(delta(content="Charting."),
            delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "create_chart", "arguments": args}}]),
            finish("tool_calls")),
        tool_text_reply("Shopee leads revenue."),
    ])
    app = create_app(gemini_settings, llm_factory=lambda s: make(s, server))
    with TestClient(app) as client:
        dataset = client.post("/api/datasets/sample").json()
        conv = client.post("/api/conversations", json={"dataset_id": dataset["id"]}).json()
        res = client.post(f"/api/conversations/{conv['id']}/messages", json={"question": "Best channel?"})
    events = [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]
    chart = next(e for e in events if e["type"] == "tool_result")
    assert chart["ui"]["spec"]["chart_type"] == "bar"
    assert events[-1]["type"] == "done" and events[-1]["ok"] and events[-1]["model"] == "gemini-test"
    second = server.requests[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[3]["tool_call_id"] == "c1"
