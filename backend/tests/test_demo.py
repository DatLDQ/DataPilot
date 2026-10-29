import pytest
from fastapi.testclient import TestClient
from test_api import parse_sse

from app.config import Settings
from app.demo import DemoLLM, make_plan, parse_schema
from app.main import create_app
from app.prompts import build_system_prompt


@pytest.fixture
def demo_client(settings):
    app = create_app(settings, llm_factory=lambda _: DemoLLM(delay_s=0))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def ask(demo_client):
    ds = demo_client.post("/api/datasets/sample").json()

    def _ask(question: str) -> list[dict]:
        conv = demo_client.post("/api/conversations", json={"dataset_id": ds["id"]}).json()
        return parse_sse(demo_client.post(f"/api/conversations/{conv['id']}/messages",
                                          json={"question": question}).text)

    return _ask


def test_schema_is_parsed_from_the_system_prompt(sample_dataset):
    _, profile = sample_dataset
    [table] = parse_schema(build_system_prompt("Sales", profile))
    assert table.name == "ecommerce_sales" and table.rows > 7000
    kinds = {c.name: c.kind for c in table.columns}
    assert kinds["order_date"] == "temporal"
    assert kinds["revenue"] == "numeric" and kinds["channel"] == "categorical"


@pytest.mark.parametrize(("question", "intent", "details"), [
    ("How has revenue changed month by month?", "trend", {"metric": "revenue", "dim": None}),
    ("Doanh thu theo tháng của từng kênh thay đổi thế nào?", "trend", {"metric": "revenue", "dim": "channel"}),
    ("Which city has the highest profit?", "breakdown", {"metric": "profit", "dim": "city", "agg": "sum"}),
    ("So sánh đánh giá trung bình giữa các danh mục", "breakdown", {"metric": "rating", "agg": "avg"}),
    ("How many orders per channel?", "breakdown", {"agg": "count", "dim": "channel"}),
    ("Does discount affect rating?", "relationship", {"x": "discount_pct", "y": "rating"}),
    ("Phân phối của delivery_days có ngoại lai không?", "distribution", {"metric": "delivery_days"}),
    ("Tổng quan dữ liệu và chất lượng", "overview", {}),
    ("Tell me a joke", "overview", {"fallback": True}),
])
def test_questions_map_to_recipes(sample_dataset, question, intent, details):
    _, profile = sample_dataset
    plan = make_plan(question, parse_schema(build_system_prompt("Sales", profile)))
    assert plan is not None and plan.intent == intent
    for attr, expected in details.items():
        value = getattr(plan, attr)
        assert (value.name if hasattr(value, "name") else value) == expected, attr


@pytest.mark.parametrize(("question", "charts", "phrase"), [
    ("How has revenue changed month by month?", 1, "strongest months"),
    ("Doanh thu theo tháng của từng kênh thay đổi thế nào?", 1, "tăng trưởng nhanh nhất"),
    ("Which city has the highest total revenue, and how concentrated is it?", 1, "top 3 account for"),
    ("Does discount affect rating?", 1, "Correlation is not causation"),
    ("What does the distribution of revenue look like, and are there outliers?", 0, "1.5×IQR"),
    ("Give me an overview of this dataset and flag any data-quality issues.", 1, "Data quality"),
])
def test_demo_agent_runs_end_to_end(ask, question, charts, phrase):
    events = ask(question)
    results = [e for e in events if e["type"] == "tool_result"]
    assert results and not [e["summary"] for e in results if e["is_error"]]
    assert sum(1 for e in results if "spec" in (e["ui"] or {})) == charts
    answer = "".join(e["text"] for e in events if e["type"] == "text_delta" and e["step"] == 2)
    assert phrase in answer and ("Demo mode" in answer or "Chế độ demo" in answer)
    done = events[-1]
    assert done["type"] == "done" and done["ok"] and done["steps"] == 2
    assert done["model"] == "demo-offline" and done["usage"]["cost_usd"] == 0


def test_demo_mode_turns_on_without_an_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    auto = Settings(_env_file=None, data_dir=tmp_path)
    assert auto.use_demo and not Settings(_env_file=None, data_dir=tmp_path, anthropic_api_key="k").use_demo
    assert not Settings(_env_file=None, data_dir=tmp_path, demo_mode=False).use_demo
    with TestClient(create_app(auto)) as c:
        health = c.get("/api/health").json()
    assert health["demo_mode"] is True and health["model"] == "demo-offline"
