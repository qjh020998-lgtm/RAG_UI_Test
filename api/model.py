"""API 层 ORM 模型。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, TEXT
from sqlalchemy.orm import Mapped, mapped_column

from api.database import Base


class DialogueStateRecord(Base):
    __tablename__ = "dialogue_states"

    sender_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state_json: Mapped[str] = mapped_column(TEXT, nullable=False, default="{}")


class ChatRecord(Base):
    """一次 /api/chat 调用及其结果。"""

    __tablename__ = "chat_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sender_id: Mapped[str] = mapped_column(String(64), index=True)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    task: Mapped[str] = mapped_column(TEXT, nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), default="unknown")
    status: Mapped[str] = mapped_column(String(16), default="error")
    content: Mapped[str] = mapped_column(TEXT, default="")
    artifacts_json: Mapped[str] = mapped_column(TEXT, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
