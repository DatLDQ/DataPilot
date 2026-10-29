import json

import pytest
from conftest import FakeLLM, text, tool_use
from fastapi.testclient import TestClient

from app.llm import LLMError
from app.main import create_app

SQL = "SELECT channel, sum(revenue) AS revenue FROM ecommerce_sales GROUP BY 1 ORDER BY 2 DESC"


def parse_sse(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


@pytest.fixture
def fake_llm():
    return FakeLLM([], suggestions=["Q1?", "Q2?", "Q3?", "Q4?"])


@pytest.fixture
def client(settings, fake_llm):
    app = create_app(settings, llm_factory=lambda _: fake_llm)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def dataset(client):
    res = client.post("/api/datasets/sample")
    assert res.status_code == 201, res.text
    return res.json()


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["api_key_configured"] is True


def test_dataset_lifecycle(client, dataset):
    assert dataset["tables"][0]["name"] == "ecommerce_sales"
    assert [d["id"] for d in client.get("/api/datasets").json()] == [dataset["id"]]
    detail = client.get(f"/api/datasets/{dataset['id']}").json()
    assert detail["profile"]["tables"][0]["row_count"] > 7000

    preview = client.get(f"/api/datasets/{dataset['id']}/preview", params={"table": "ecommerce_sales", "limit": 7})
    assert len(preview.json()["rows"]) == 7
    assert client.get(f"/api/datasets/{dataset['id']}/preview", params={"table": "x; drop"}).status_code == 404

    sugg = client.get(f"/api/datasets/{dataset['id']}/suggestions").json()
    assert sugg == {"suggestions": ["Q1?", "Q2?", "Q3?", "Q4?"], "source": "llm"}

    conv = client.post("/api/conversations", json={"dataset_id": dataset["id"]}).json()
    assert client.delete(f"/api/datasets/{dataset['id']}").status_code == 204
    assert client.get("/api/datasets").json() == []
    assert client.get(f"/api/conversations/{conv['id']}").status_code == 404  # cascaded


def test_upload(client, tmp_path):
    f = tmp_path / "Doanh số.csv"
    f.write_text("Ngày,Khu vực,Doanh thu\n2025-01-01,Bắc,10\n2025-01-02,Nam,20\n", encoding="utf-8")
    with f.open("rb") as fh:
        res = client.post("/api/datasets", files={"file": (f.name, fh, "text/csv")})
    assert res.status_code == 201, res.text
    assert res.json()["name"] == "Doanh số"
    detail = client.get(f"/api/datasets/{res.json()['id']}").json()
    assert [c["name"] for c in detail["profile"]["tables"][0]["columns"]] == ["ngay", "khu_vuc", "doanh_thu"]

    assert client.post("/api/datasets", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert client.post("/api/datasets", files={"file": ("e.csv", b"", "text/csv")}).status_code == 400


def test_heuristic_suggestions_when_llm_unavailable(settings):
    def broken(_):
        raise LLMError("no key")

    with TestClient(create_app(settings, llm_factory=broken)) as c:
        ds = c.post("/api/datasets/sample").json()
        body = c.get(f"/api/datasets/{ds['id']}/suggestions").json()
        assert body["source"] == "heuristic" and len(body["suggestions"]) >= 3

        conv = c.post("/api/conversations", json={"dataset_id": ds["id"]}).json()
        events = parse_sse(c.post(f"/api/conversations/{conv['id']}/messages", json={"question": "hi"}).text)
        assert [e["type"] for e in events] == ["run_start", "error", "done"]
        assert events[1]["message"] == "no key"


def test_ask_streams_and_persists(client, dataset, fake_llm):
    fake_llm.script = [
        ([text("Checking."), tool_use("t1", "create_chart", title="Revenue by channel", chart_type="bar",
                                      sql=SQL, x="channel", y=["revenue"])], "tool_use"),
        ([text("Shopee leads revenue.")], "end_turn"),
        ([text("Follow-up answer.")], "end_turn"),
    ]
    conv = client.post("/api/conversations", json={"dataset_id": dataset["id"]}).json()
    url = f"/api/conversations/{conv['id']}/messages"

    res = client.post(url, json={"question": "Which channel brings the most revenue?"})
    assert res.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(res.text)
    chart = next(e for e in events if e["type"] == "tool_result")
    assert chart["ui"]["spec"]["chart_type"] == "bar" and len(chart["ui"]["data"]) == 5
    assert events[-1]["type"] == "done" and events[-1]["ok"]

    stored = client.get(f"/api/conversations/{conv['id']}").json()
    assert stored["title"] == "Which channel brings the most revenue?"
    assert len(stored["turns"]) == 1
    replay = stored["turns"][0]["events"]
    assert "".join(e["text"] for e in replay if e["type"] == "text_delta").strip() == "Checking. Shopee leads revenue."

    # Second question replays the persisted history (user, assistant, tool results, assistant).
    client.post(url, json={"question": "And by city?"})
    assert len(fake_llm.calls[-1]["messages"]) == 5
    assert len(client.get(f"/api/conversations/{conv['id']}").json()["turns"]) == 2


def test_failed_turn_does_not_corrupt_history(client, dataset, fake_llm):
    fake_llm.script = [LLMError("boom"), ([text("ok")], "end_turn")]
    conv = client.post("/api/conversations", json={"dataset_id": dataset["id"]}).json()
    url = f"/api/conversations/{conv['id']}/messages"
    events = parse_sse(client.post(url, json={"question": "q1"}).text)
    assert any(e["type"] == "error" for e in events)
    client.post(url, json={"question": "q2"})
    assert [m["role"] for m in fake_llm.calls[-1]["messages"]] == ["user"]


def test_busy_conversation_rejected(client, dataset):
    conv = client.post("/api/conversations", json={"dataset_id": dataset["id"]}).json()
    client.app.state.datapilot.busy.add(conv["id"])
    assert client.post(f"/api/conversations/{conv['id']}/messages", json={"question": "q"}).status_code == 409


def test_conversation_crud(client, dataset):
    conv = client.post("/api/conversations", json={"dataset_id": dataset["id"]}).json()
    assert client.patch(f"/api/conversations/{conv['id']}", json={"title": "Renamed"}).json()["title"] == "Renamed"
    assert len(client.get("/api/conversations", params={"dataset_id": dataset["id"]}).json()) == 1
    assert client.delete(f"/api/conversations/{conv['id']}").status_code == 204
    assert client.post("/api/conversations", json={"dataset_id": "000000000000"}).status_code == 404
