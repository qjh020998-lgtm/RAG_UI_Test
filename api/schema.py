"""API 请求 / 响应模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    sender_id: str = Field(min_length=1, description="用户 id；用于用户画像稳定存储")
    task: str = Field(min_length=1, description="用户输入的生成任务")
    message_id: str | None = Field(default=None, description="前端消息 id；不传则自动生成")
    thread_id: str | None = Field(
        default=None,
        description="会话 id；工作记忆/情景记忆按它隔离，不传时使用 sender_id",
    )


class ArtifactOut(BaseModel):
    kind: Literal["json", "xlsx"]
    path: str = Field(description="相对项目根目录的路径")
    url: str = Field(description="可直接访问的静态文件地址")


class ChatResponse(BaseModel):
    run_id: str
    sender_id: str
    thread_id: str = ""
    message_id: str
    agent_type: str
    status: Literal["success", "degraded", "error"]
    content: str
    artifacts: list[ArtifactOut] = Field(default_factory=list)


class HistoryMessage(BaseModel):
    role: str
    text: str | None = None


class HistoryResponse(BaseModel):
    sender_id: str
    messages: list[HistoryMessage] = Field(default_factory=list)
