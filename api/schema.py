"""API 请求 / 响应模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    sender_id: str = Field(min_length=1, description="用户 id")
    task: str = Field(min_length=1, description="用户输入的生成任务")
    message_id: str | None = Field(default=None, description="前端消息 id；不传则自动生成")


class ArtifactOut(BaseModel):
    kind: Literal["json", "xlsx"]
    path: str = Field(description="相对项目根目录的路径")
    url: str = Field(description="可直接访问的静态文件地址")


class ChatResponse(BaseModel):
    run_id: str
    sender_id: str
    message_id: str
    agent_type: str
    status: Literal["success", "error"]
    content: str
    artifacts: list[ArtifactOut] = Field(default_factory=list)


class HistoryMessage(BaseModel):
    role: str
    text: str | None = None


class HistoryResponse(BaseModel):
    sender_id: str
    messages: list[HistoryMessage] = Field(default_factory=list)
