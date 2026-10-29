"""FastAPI application: dataset management, conversations and the streaming agent endpoint.

Run with:  uvicorn app.main:create_app --factory --reload
"""

import asyncio
import json
import logging
import os
import sqlite3
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .agent import AgentRunner, compact_events
from .config import Settings, get_settings
from .datasets import DatasetError, DatasetManager, quote_ident
from .demo import DEMO_MODEL, DemoLLM, columns_from_profile, pick_metric
from .llm import ClaudeLLM, LLMClient, LLMError
from .openai_compat import OpenAICompatLLM
from .prompts import SUGGESTIONS_PROMPT, build_system_prompt, render_schema
from .sample_data import ensure_sample
from .schemas import AskRequest, CreateConversation, RenameConversation, SampleRequest, summarize_dataset
from .store import Store
from .tools import ToolExecutor

logger = logging.getLogger("datapilot")

class AppState:
    def __init__(self, settings: Settings, llm_factory: Callable[[Settings], LLMClient]):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(settings.data_dir / "datapilot.db")
        self.datasets = DatasetManager(settings.data_dir, settings.sql_timeout_s)
        self._llm_factory = llm_factory
        self._llm: LLMClient | None = None
        self.busy: set[str] = set()

    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = self._llm_factory(self.settings)
        return self._llm

    def close(self) -> None:
        self.datasets.close()
        self.store.close()


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def heuristic_suggestions(profile: dict) -> list[str]:
    columns = columns_from_profile(profile["tables"][0])
    date_col = next((c.name for c in columns if c.kind == "temporal"), None)
    cat_col = next((c.name for c in columns if c.kind == "categorical"), None)
    metric = pick_metric(columns)
    num_col = metric.name if metric else None
    out = ["Give me an overview of this dataset and flag any data-quality issues."]
    if date_col and num_col:
        out.append(f"How has total {num_col} changed month by month?")
    if cat_col and num_col:
        out.append(f"Which {cat_col} has the highest total {num_col}, and how concentrated is it?")
    elif cat_col:
        out.append(f"What is the breakdown of records by {cat_col}?")
    if num_col:
        out.append(f"What does the distribution of {num_col} look like, and are there outliers?")
    return out[:4]


def make_llm(settings: Settings) -> LLMClient:
    if settings.use_demo:
        return DemoLLM()
    if settings.active_provider == "claude":
        return ClaudeLLM(settings)
    return OpenAICompatLLM(settings)


def _error_run(message: str, model: str) -> list[dict[str, Any]]:
    return [
        {"type": "run_start", "step": 0, "model": model},
        {"type": "error", "step": 0, "message": message},
        {"type": "done", "step": 0, "ok": False, "stop_reason": None, "steps": 0, "model": model,
         "usage": None, "elapsed_ms": 0},
    ]


def create_app(settings: Settings | None = None, llm_factory: Callable[[Settings], LLMClient] = make_llm) -> FastAPI:
    settings = settings or get_settings()
    model = DEMO_MODEL if settings.use_demo else settings.active_model
    state = AppState(settings, llm_factory)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        state.close()

    app = FastAPI(title="DataPilot API", version="1.0.0", lifespan=lifespan)
    app.state.datapilot = state
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_dataset_or_404(dataset_id: str) -> dict:
        ds = state.store.get_dataset(dataset_id)
        if ds is None:
            raise HTTPException(404, "Dataset not found.")
        return ds

    def get_conversation_or_404(conv_id: str) -> dict:
        conv = state.store.get_conversation(conv_id)
        if conv is None:
            raise HTTPException(404, "Conversation not found.")
        return conv

    async def ingest(path: Path, filename: str, name: str) -> dict:
        try:
            dataset_id, profile = await asyncio.to_thread(state.datasets.ingest, path, filename)
        except DatasetError as exc:
            raise HTTPException(400, str(exc)) from exc
        return state.store.add_dataset(dataset_id, name, filename, profile)

    # -- health -------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "provider": "demo" if settings.use_demo else settings.active_provider,
            "model": model,
            "effort": settings.effort,
            "api_key_configured": settings.api_key_configured,
            "demo_mode": settings.use_demo,
        }

    # -- datasets -----------------------------------------------------------
    @app.post("/api/datasets", status_code=201)
    async def upload_dataset(file: Annotated[UploadFile, File()]) -> dict:
        filename = Path(file.filename or "upload.csv").name
        limit = settings.max_upload_mb * 1024 * 1024
        tmp_dir = settings.data_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(suffix=Path(filename).suffix.lower(), dir=tmp_dir)
        tmp = Path(tmp_name)
        try:
            size = 0
            with os.fdopen(fd, "wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB.")
                    out.write(chunk)
            if size == 0:
                raise HTTPException(400, "The uploaded file is empty.")
            ds = await ingest(tmp, filename, Path(filename).stem)
        finally:
            tmp.unlink(missing_ok=True)
        return summarize_dataset(ds).model_dump()

    @app.post("/api/datasets/sample", status_code=201)
    async def load_sample(body: SampleRequest | None = None) -> dict:
        path = await asyncio.to_thread(ensure_sample, settings.samples_dir)
        ds = await ingest(path, path.name, (body or SampleRequest()).name)
        return summarize_dataset(ds).model_dump()

    @app.get("/api/datasets")
    def list_datasets() -> list[dict]:
        return [summarize_dataset(ds).model_dump() for ds in state.store.list_datasets()]

    @app.get("/api/datasets/{dataset_id}")
    def get_dataset(dataset_id: str) -> dict:
        ds = get_dataset_or_404(dataset_id)
        return {**summarize_dataset(ds).model_dump(), "profile": ds["profile"]}

    @app.get("/api/datasets/{dataset_id}/preview")
    async def preview(dataset_id: str, table: str, limit: int = Query(50, ge=1, le=500)) -> dict:
        ds = get_dataset_or_404(dataset_id)
        if table not in {t["name"] for t in ds["profile"]["tables"]}:
            raise HTTPException(404, "Table not found.")
        result = await asyncio.to_thread(
            state.datasets.query, dataset_id, f"SELECT * FROM {quote_ident(table)} LIMIT {limit}", limit, False
        )
        return {"columns": result.columns, "column_types": result.column_types, "rows": result.rows}

    @app.get("/api/datasets/{dataset_id}/suggestions")
    async def suggestions(dataset_id: str) -> dict:
        ds = get_dataset_or_404(dataset_id)
        if ds["suggestions"]:
            return {"suggestions": ds["suggestions"], "source": "llm"}
        try:
            prompt = SUGGESTIONS_PROMPT.format(schema=render_schema(ds["profile"]))
            questions = await asyncio.wait_for(state.llm().suggest_questions(prompt), timeout=45)
        except (LLMError, TimeoutError) as exc:
            logger.warning("Falling back to heuristic suggestions: %s", exc)
            questions = []
        if questions:
            state.store.set_suggestions(dataset_id, questions)
            return {"suggestions": questions, "source": "llm"}
        return {"suggestions": heuristic_suggestions(ds["profile"]), "source": "heuristic"}

    @app.delete("/api/datasets/{dataset_id}", status_code=204)
    async def delete_dataset(dataset_id: str) -> None:
        get_dataset_or_404(dataset_id)
        state.store.delete_dataset(dataset_id)
        await asyncio.to_thread(state.datasets.delete, dataset_id)

    # -- conversations ------------------------------------------------------
    @app.get("/api/conversations")
    def list_conversations(dataset_id: str | None = None) -> list[dict]:
        return state.store.list_conversations(dataset_id)

    @app.post("/api/conversations", status_code=201)
    def create_conversation(body: CreateConversation) -> dict:
        get_dataset_or_404(body.dataset_id)
        return state.store.create_conversation(body.dataset_id)

    @app.get("/api/conversations/{conv_id}")
    def get_conversation(conv_id: str) -> dict:
        conv = get_conversation_or_404(conv_id)
        return {**conv, "turns": state.store.get_turns(conv_id), "busy": conv_id in state.busy}

    @app.patch("/api/conversations/{conv_id}")
    def rename_conversation(conv_id: str, body: RenameConversation) -> dict:
        get_conversation_or_404(conv_id)
        state.store.rename_conversation(conv_id, body.title.strip())
        return state.store.get_conversation(conv_id)  # type: ignore[return-value]

    @app.delete("/api/conversations/{conv_id}", status_code=204)
    def delete_conversation(conv_id: str) -> None:
        get_conversation_or_404(conv_id)
        if conv_id in state.busy:
            raise HTTPException(409, "This conversation is still running.")
        state.store.delete_conversation(conv_id)

    @app.post("/api/conversations/{conv_id}/messages")
    async def ask(conv_id: str, body: AskRequest) -> StreamingResponse:
        conv = get_conversation_or_404(conv_id)
        ds = get_dataset_or_404(conv["dataset_id"])
        if conv_id in state.busy:
            raise HTTPException(409, "This conversation is already answering a question.")
        state.busy.add(conv_id)
        question = body.question.strip()
        history = state.store.get_api_messages(conv_id)
        if not state.store.get_turns(conv_id):
            title = question if len(question) <= 60 else question[:57].rstrip() + "..."
            state.store.rename_conversation(conv_id, title)

        async def stream() -> AsyncIterator[str]:
            runner: AgentRunner | None = None
            events: list[dict[str, Any]] = []
            try:
                try:
                    llm = state.llm()
                except LLMError as exc:
                    events = _error_run(str(exc), model)
                    for ev in events:
                        yield sse(ev)
                    return
                executor = ToolExecutor(state.datasets, ds["id"], ds["profile"], settings)
                runner = AgentRunner(llm, executor, settings)
                events = runner.events
                system = build_system_prompt(ds["name"], ds["profile"])
                async for ev in runner.run(history, question, system):
                    yield sse(ev)
            finally:
                if events and events[-1]["type"] != "done":
                    events.append({"type": "error", "step": events[-1].get("step", 0), "message": "Stopped."})
                    events.append({"type": "done", "step": events[-2].get("step", 0), "ok": False,
                                   "stop_reason": "cancelled", "steps": runner.step if runner else 0,
                                   "model": model, "usage": None, "elapsed_ms": 0})
                try:
                    if runner is not None and runner.completed:
                        state.store.append_api_messages(conv_id, runner.new_messages)
                    state.store.add_turn(conv_id, question, compact_events(events))
                    state.store.touch_conversation(conv_id)
                except sqlite3.IntegrityError:
                    pass  # conversation was deleted while the agent ran
                state.busy.discard(conv_id)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app

