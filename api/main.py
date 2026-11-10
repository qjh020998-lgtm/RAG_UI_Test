"""FastAPI 入口：对话生成任务。"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from agent.runner import AgentRunner
from api.database import Base, engine, get_db
from api.model import ChatRecord
from api.schema import ArtifactOut, ChatRequest, ChatResponse
from tool.context import configure
from utils.logger import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def _collect_artifacts(started_at: float) -> list[ArtifactOut]:
    """收集本次运行期间生成的 JSON / xlsx。"""
    artifacts: list[ArtifactOut] = []
    for pattern, kind in (("*.json", "json"), ("*.xlsx", "xlsx")):
        for path in ARTIFACTS_DIR.rglob(pattern):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < started_at - 1:
                    continue
            except OSError:
                continue

            relative = path.relative_to(PROJECT_ROOT).as_posix()
            url_path = path.relative_to(ARTIFACTS_DIR).as_posix()
            artifacts.append(ArtifactOut(
                kind=kind,
                path=relative,
                url=f"/artifacts/{url_path}",
            ))

    return sorted(artifacts, key=lambda item: item.path)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    configure(
        workspace=PROJECT_ROOT,
        artifacts=ARTIFACTS_DIR,
        config={"headless": True},
    )
    app.state.runner = AgentRunner()
    app.state.run_lock = asyncio.Lock()
    yield


app = FastAPI(title="ERP Agent API", lifespan=lifespan)
app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "chat.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    message_id = payload.message_id or str(uuid4())
    started_at = time.time()

    # ToolContext / 浏览器是进程级资源，先串行执行，后续再考虑并发隔离。
    async with request.app.state.run_lock:
        result = await request.app.state.runner.run(payload.task)

    artifacts = _collect_artifacts(started_at)
    status = str(result.get("status") or "error")
    content = str(result.get("content") or "")

    record = ChatRecord(
        id=str(uuid4()),
        sender_id=payload.sender_id,
        message_id=message_id,
        task=payload.task,
        agent_type=str(result.get("agent_type") or "unknown"),
        status=status,
        content=content,
        artifacts_json=json.dumps(
            [artifact.model_dump() for artifact in artifacts],
            ensure_ascii=False,
        ),
        created_at=datetime.utcnow(),
    )
    db.add(record)
    await db.commit()

    return ChatResponse(
        run_id=record.id,
        sender_id=payload.sender_id,
        message_id=message_id,
        agent_type=record.agent_type,
        status="success" if status == "success" else "error",
        content=content,
        artifacts=artifacts,
    )
