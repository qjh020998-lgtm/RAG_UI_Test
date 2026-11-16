"""HistoryManager - 历史消息管理器（工作记忆 / 短期记忆）

职责：
- 消息追加（只追加，不编辑，缓存友好）
- 历史压缩（生成 summary + 保留最近消息）
- 会话序列化/反序列化
- 轮次边界检测

工作记忆只存在于内存里，随会话删除而消失；摘要本身由 ContextBuilder
生成并合并进情景记忆，这里只负责“摘要 + 最近 N 条”的裁剪。
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from .message import Message

WORKING_MAX = 20    # 工作记忆最大条数，超过则触发压缩（压缩失败时强制裁剪）
COMPRESS_AT = 15    # 达到此条数时压缩，保留摘要 + 最近 5 条
KEEP_RECENT = 5     # 压缩后保留的最近消息条数


class HistoryManager:
    """工作记忆管理器

    特性：
    - 只追加，不编辑（缓存友好）
    - 达到 ``compress_at`` 后由 ContextBuilder 压缩（summary + 最近 N 条）
    - 同一会话始终最多一条 summary，不会无限拼接
    - 支持会话保存/加载

    用法示例：
    ```python
    manager = HistoryManager()

    # 追加消息
    manager.append(Message("hello", "user"))
    manager.append(Message("hi", "assistant"))

    # 获取历史
    history = manager.get_history()

    # 达到阈值后压缩（summary 由 ContextBuilder 生成）
    if manager.should_compress():
        manager.compress("这是前面对话的摘要")

    # 序列化
    data = manager.to_dict()

    # 反序列化
    manager.load_from_dict(data)
    ```
    """

    def __init__(self,
                 *,
                 compress_at: int = COMPRESS_AT,
                 max_messages: int = WORKING_MAX,
                 keep_recent: int = KEEP_RECENT):
        if keep_recent <= 0 or compress_at <= keep_recent:
            raise ValueError("compress_at 必须大于 keep_recent，且 keep_recent 大于 0")
        if max_messages < compress_at:
            raise ValueError("max_messages 不能小于 compress_at")
        self.compress_at = compress_at
        self.max_messages = max_messages
        self.keep_recent = keep_recent
        self.created_at = datetime.now()
        self.history: List[Message] = []

    def __len__(self) -> int:
        return len(self.history)

    def append(self, message: Message):
        """追加消息（只追加，不编辑）

        Args:
           message: 要追加的消息
        """
        self.history.append(message)

    def get_history(self) -> List[Message]:
        """获取历史消息

        Returns:
           List[Message]: 历史消息列表
        """
        return self.history.copy()

    def clear(self) -> None:
        """清空历史"""
        self.history.clear()

    def should_compress(self) -> bool:
        """是否达到压缩阈值（且确实有可归档消息）。"""
        return len(self.history) >= self.compress_at and bool(self.messages_to_archive())

    def messages_to_archive(self) -> List[Message]:
        """需要归档成摘要的旧消息。

        排除最近 ``keep_recent`` 条，也排除已有 summary（summary 已由
        ContextBuilder 合并更新到情景记忆里，不再重复摘要）。
        """
        if len(self.history) <= self.keep_recent:
            return []
        candidates = self.history[:-self.keep_recent]
        return [message for message in candidates if message.role != "summary"]

    def compress(self, summary: Optional[str] = None) -> bool:
        """压缩历史

        将旧历史替换为 summary 消息，保留最近 N 条完整对话。
        同一会话永远只有一条 summary，重复压缩是覆盖而不是追加。

        Args:
            summary: 历史摘要文本（由 ContextBuilder 生成，已包含旧摘要信息）

        Returns:
            是否发生了压缩
        """
        summary = (summary or "").strip()
        archived = self.messages_to_archive()
        if not summary or not archived:
            return False

        summary_msg = Message(
            content=summary,
            role="summary",
            metadata={
                "compressed_at": datetime.now().isoformat(),
                "archived_messages": len(archived),
            },
        )
        kept = [msg for msg in self.history[-self.keep_recent:] if msg.role != "summary"]
        self.history = [summary_msg] + kept
        return True

    def enforce_limit(self) -> None:
        """硬上限兜底：摘要生成失败时只保留 summary + 最近消息。

        正常路径走 :meth:`compress`；这里只在模型/存储不可用时避免工作记忆
        无限增长（会丢失旧消息，但不阻塞请求）。
        """
        if len(self.history) <= self.max_messages:
            return

        summary = next((msg for msg in self.history if msg.role == "summary"), None)
        keep = max(1, self.max_messages - (1 if summary else 0))
        kept = [msg for msg in self.history[-keep:] if msg.role != "summary"]
        self.history = ([summary] if summary else []) + kept

    def estimate_rounds(self) -> int:
        """预估完整轮次数

        一轮定义：1 user 消息 + N 条 assistant/tool/summary 消息

        Returns:
            完整轮次数
        """
        rounds = 0
        i = 0
        while i < len(self.history):
            if self.history[i].role == "user":
                rounds += 1
                # 跳过这一轮的后续消息
                i += 1
                while i < len(self.history) and self.history[i].role != "user":
                    i += 1
            else:
                i += 1
        return rounds

    def find_round_boundaries(self) -> List[int]:
        """查找每轮的起始索引

        Returns:
            每轮起始索引列表，例如 [0, 3, 7, 10]
        """
        boundaries = []
        for i, msg in enumerate(self.history):
            if msg.role == "user":
                boundaries.append(i)
        return boundaries

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于会话保存）

        Returns:
            包含历史和元数据的字典
        """
        return {
            "history": [msg.to_dict() for msg in self.history],
            "created_at": self.created_at.isoformat(),
            "rounds": self.estimate_rounds()
        }

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        """从字典加载（用于会话恢复）

        Args:
            data: 序列化的历史数据
        """
        self.history = [
            Message.from_dict(msg_data)
            for msg_data in data.get("history", [])
        ]
        created_at = data.get("created_at")
        if created_at:
            try:
                self.created_at = datetime.fromisoformat(created_at)
            except ValueError:
                self.created_at = datetime.now()
