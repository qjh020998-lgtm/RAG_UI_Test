"""FastAPI 入口：对话生成任务。"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi import status as http_status
from fastapi.responses import FileResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent.agents import ERPAgentHub
from agent.orchestrator import AgentOrchestrator, AgentRequest
from api.database import AsyncSessionLocal, Base, engine, get_db
from api.model import ChatRecord
from api.schema import ArtifactOut, ChatRequest, ChatResponse
from tool.context import configure
from utils.logger import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ChatStreamContext:
    """流式接口在返回 Response 前完成校验后的上下文。"""

    task: str
    thread_id: str
    message_id: str
    run_id: str
    sender_id: str
    started_at: float
    orchestrator: AgentOrchestrator
    run_lock: asyncio.Lock


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

    app.state.orchestrator = AgentOrchestrator(hub=ERPAgentHub())
    app.state.run_lock = asyncio.Lock()
    yield


app = FastAPI(title="ERP Agent API", lifespan=lifespan)
app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(
        FRONTEND_DIR / "console.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/chat", include_in_schema=False)
async def simple_chat() -> FileResponse:
    return FileResponse(
        FRONTEND_DIR / "chat.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/agent_stream.js", include_in_schema=False)
async def agent_stream_js() -> FileResponse:
    return FileResponse(
        FRONTEND_DIR / "agent_stream.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


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
    thread_id = payload.thread_id or payload.sender_id
    started_at = time.time()

    task = payload.task.strip()
    if not task:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="task 不能为空或只包含空白字符",
        )

    orchestrator = getattr(request.app.state, "orchestrator", None)
    run_lock = getattr(request.app.state, "run_lock", None)
    if orchestrator is None or run_lock is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent 编排器尚未初始化",
        )

    # 同一个 message_id 视为重复提交，避免重复执行有副作用的 Agent 任务。
    try:
        existing_run_id = await db.scalar(
            select(ChatRecord.id).where(ChatRecord.message_id == message_id)
        )
    except SQLAlchemyError as exc:
        logger.exception("查询聊天记录失败")
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="聊天记录存储不可用",
        ) from exc

    if existing_run_id:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"message_id 已存在，对应 run_id={existing_run_id}",
        )

    # ToolContext / 浏览器是进程级资源，先串行执行，后续再考虑并发隔离。
    try:
        async with run_lock:
            result = await orchestrator.run(
                AgentRequest(
                    task=task,
                    thread_id=thread_id,
                    user_id=payload.sender_id,
                )
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("编排器执行失败：%s", task)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent 编排器执行失败",
        ) from exc

    artifacts = _collect_artifacts(started_at)

    record = ChatRecord(
        id=str(uuid4()),
        sender_id=payload.sender_id,
        message_id=message_id,
        task=task,
        agent_type=result.agent_type,
        status=result.status,
        content=result.content,
        artifacts_json=json.dumps(
            [artifact.model_dump() for artifact in artifacts],
            ensure_ascii=False,
        ),
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    try:
        db.add(record)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="message_id 已存在，请勿重复提交",
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("聊天记录写入失败")
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="聊天记录写入失败",
        ) from exc

    if result.status == "error":
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Agent 执行失败（run_id={record.id}）："
                f"{result.content[:500]}"
            ),
        )

    return ChatResponse(
        run_id=record.id,
        sender_id=payload.sender_id,
        thread_id=thread_id,
        message_id=message_id,
        agent_type=result.agent_type,
        status=result.status,
        content=result.content,
        artifacts=artifacts,
    )


async def prepare_chat_stream(
    payload: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ChatStreamContext:
    """在 SSE 响应开始前完成校验和重复提交检查。"""
    message_id = payload.message_id or str(uuid4())
    thread_id = payload.thread_id or payload.sender_id
    started_at = time.time()

    task = payload.task.strip()
    if not task:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="task 不能为空或只包含空白字符",
        )

    orchestrator = getattr(request.app.state, "orchestrator", None)
    run_lock = getattr(request.app.state, "run_lock", None)
    if orchestrator is None or run_lock is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent 编排器尚未初始化",
        )

    try:
        existing_run_id = await db.scalar(
            select(ChatRecord.id).where(ChatRecord.message_id == message_id)
        )
    except SQLAlchemyError as exc:
        logger.exception("查询聊天记录失败")
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="聊天记录存储不可用",
        ) from exc

    if existing_run_id:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"message_id 已存在，对应 run_id={existing_run_id}",
        )

    return ChatStreamContext(
        task=task,
        thread_id=thread_id,
        message_id=message_id,
        run_id=str(uuid4()),
        sender_id=payload.sender_id,
        started_at=started_at,
        orchestrator=orchestrator,
        run_lock=run_lock,
    )


@app.post("/api/chat/stream", response_class=EventSourceResponse)
async def chat_stream(
    ctx: ChatStreamContext = Depends(prepare_chat_stream),
) -> AsyncIterator[ServerSentEvent]:
    """SSE 流式对话：meta -> delta* -> done/error。"""
    status = "error"
    content = ""
    agent_type = "unknown"
    route_reason = ""

    try:
        async with ctx.run_lock:
            async for event in ctx.orchestrator.astream(
                AgentRequest(
                    task=ctx.task,
                    thread_id=ctx.thread_id,
                    user_id=ctx.sender_id,
                    request_id=ctx.run_id,
                )
            ):
                event_type = event.get("type")
                if event_type == "meta":
                    agent_type = str(event.get("agent_type") or "unknown")
                    route_reason = str(event.get("route_reason") or "")
                    yield ServerSentEvent(
                        event="meta",
                        data={**event, "run_id": ctx.run_id},
                    )
                elif event_type == "delta":
                    content += str(event.get("text") or "")
                    yield ServerSentEvent(event="delta", data=event)
                elif event_type == "done":
                    content = str(event.get("content") or content)
                    status = str(event.get("status") or "success")
                    agent_type = str(event.get("agent_type") or agent_type)
                    route_reason = str(event.get("route_reason") or route_reason)
                    break
                elif event_type == "error":
                    partial = str(event.get("partial") or "")
                    error_content = str(event.get("content") or "")
                    if partial and error_content:
                        content = f"{partial}\n\n{error_content}"
                    else:
                        content = partial or error_content or content
                    status = "error"
                    agent_type = str(event.get("agent_type") or agent_type)
                    route_reason = str(event.get("route_reason") or route_reason)
                    break
    except Exception as exc:
        logger.exception("流式编排失败：%s", ctx.task)
        status = "error"
        content = (content + "\n\n" if content else "") + f"执行失败：{exc}"

    if status == "error" and not content:
        content = "Agent 没有返回完整结果"

    artifacts = _collect_artifacts(ctx.started_at)
    record = ChatRecord(
        id=ctx.run_id,
        sender_id=ctx.sender_id,
        message_id=ctx.message_id,
        task=ctx.task,
        agent_type=agent_type,
        status=status,
        content=content,
        artifacts_json=json.dumps(
            [artifact.model_dump() for artifact in artifacts],
            ensure_ascii=False,
        ),
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    try:
        async with AsyncSessionLocal() as session:
            session.add(record)
            await session.commit()
    except IntegrityError:
        yield ServerSentEvent(
            event="error",
            data={
                "type": "error",
                "run_id": ctx.run_id,
                "status": "error",
                "content": "message_id 已存在，请勿重复提交",
                "agent_type": agent_type,
            },
        )
        return
    except SQLAlchemyError:
        logger.exception("流式聊天记录写入失败")
        yield ServerSentEvent(
            event="error",
            data={
                "type": "error",
                "run_id": ctx.run_id,
                "status": "error",
                "content": "聊天记录写入失败",
                "agent_type": agent_type,
            },
        )
        return

    event_name = "done" if status != "error" else "error"
    yield ServerSentEvent(
        event=event_name,
        data={
            "type": event_name,
            "run_id": ctx.run_id,
            "status": status,
            "content": content,
            "agent_type": agent_type,
            "route_reason": route_reason,
            "artifacts": [artifact.model_dump() for artifact in artifacts],
        },
    )
