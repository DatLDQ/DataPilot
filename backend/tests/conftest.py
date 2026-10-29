import copy
from pathlib import Path

import pytest

from app.config import Settings
from app.datasets import DatasetManager
from app.llm import LLMEvent, LLMTurn
from app.sample_data import write_sample_csv


class FakeLLM:
    """Scripted stand-in for ClaudeLLM. Each script entry is one model turn:
    ``(content_blocks, stop_reason)``, or an exception instance to raise mid-stream."""

    model = "claude-opus-5-5"

    def __init__(self, script: list, suggestions: list[str] | None = None):
        self.script = list(script)
        self.calls: list[dict] = []
        self.suggestions = suggestions or []

    async def stream(self, *, system, tools, messages, tool_choice=None):
        self.calls.append({"system": system, "messages": copy.deepcopy(messages), "tool_choice": tool_choice})
        entry = self.script.pop(0)
        if isinstance(entry, Exception):
            yield LLMEvent("text_start")
            raise entry
        content, stop_reason = entry
        for block in content:
            if block["type"] == "thinking":
                yield LLMEvent("thinking_start")
                yield LLMEvent("thinking_delta", {"text": block["thinking"]})
            elif block["type"] == "text":
                yield LLMEvent("text_start")
                for word in block["text"].split(" "):
                    yield LLMEvent("text_delta", {"text": word + " "})
            elif block["type"] == "tool_use":
                yield LLMEvent("tool_use_start", {"id": block["id"], "name": block["name"]})
        yield LLMTurn(
            content=content,
            stop_reason=stop_reason,
            model=self.model,
            usage={"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500,
                   "cache_creation_input_tokens": 0},
        )

    async def suggest_questions(self, prompt: str) -> list[str]:
        return self.suggestions


def tool_use(id_: str, name: str, **inputs) -> dict:
    return {"type": "tool_use", "id": id_, "name": name, "input": inputs}


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def thinking(t: str) -> dict:
    return {"type": "thinking", "thinking": t, "signature": "sig"}


@pytest.fixture(scope="session")
def sample_csv(tmp_path_factory) -> Path:
    return write_sample_csv(tmp_path_factory.mktemp("samples") / "ecommerce_sales.csv")


@pytest.fixture
def settings(tmp_path, sample_csv) -> Settings:
    return Settings(
        _env_file=None,
        anthropic_api_key="test-key",
        data_dir=tmp_path / "data",
        samples_dir=sample_csv.parent,
        max_agent_steps=3,
    )


@pytest.fixture
def manager(settings) -> DatasetManager:
    m = DatasetManager(settings.data_dir, sql_timeout_s=5)
    yield m
    m.close()


@pytest.fixture
def sample_dataset(manager, sample_csv) -> tuple[str, dict]:
    return manager.ingest(sample_csv, "ecommerce_sales.csv")
