from typing import Optional, Dict, Any, Literal
from datetime import datetime
from pydantic import BaseModel, Field

MessageRole = Literal["user", "assistant", "system", "tool", "summary"]

class Message(BaseModel):
    """消息类"""

    content: str
    role: MessageRole
    timestamp: Optional[datetime] = Field(default_factory=datetime.now)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    def __init__(self, content: str, role: MessageRole, **kwargs):
        kwargs.setdefault("timestamp", datetime.now())
        kwargs.setdefault("metadata", {})
        super().__init__(content=content, role=role, **kwargs)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式（OpenAI API格式）"""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """从字典创建消息对象"""
        timestamp = data.get("timestamp")
        if timestamp and isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        return cls(
            content=data["content"],
            role=data["role"],
            timestamp=timestamp,
            metadata=data.get("metadata")
        )

    def to_text(self) -> str:
        """格式化为文本（用于上下文构建）"""
        return f"[{self.role}] {self.content}"

    def to_model_dict(self) -> Dict[str, str]:
        """转换为发给模型的消息（只保留 role / content）。

        ``summary`` / ``tool`` 不是模型可接受的角色：
        summary 降级为 system，tool 结果降级为 user。
        """
        if self.role == "summary":
            return {
                "role": "system",
                "content": f"## 历史对话摘要\n{self.content}",
            }
        if self.role == "tool":
            return {"role": "user", "content": f"[工具结果] {self.content}"}
        return {"role": self.role, "content": self.content}

    def __str__(self) -> str:
        return f"[{self.role}] {self.content}"
