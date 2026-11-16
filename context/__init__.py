"""上下文层：把三层记忆汇入 :class:`ContextBuilder`，拼成发给模型的消息。

- 工作记忆：:class:`HistoryManager`（内存，随会话消失）；
- 情景记忆：``memory.MemoryStore``（ChromaDB ``episodic``）；
- 用户画像：``memory.UserProfile``（ChromaDB ``user_profile``）。
"""

from .builder import (
    COMPRESS_AT,
    CONTEXT_MAX_TOKENS,
    HISTORY_TOP_K,
    KEEP_RECENT,
    WORKING_MAX,
    ContextBuilder,
    ContextOverflowError,
    estimate_tokens,
)
from .history import HistoryManager
from .message import Message, MessageRole

__all__ = [
    "COMPRESS_AT",
    "CONTEXT_MAX_TOKENS",
    "HISTORY_TOP_K",
    "KEEP_RECENT",
    "WORKING_MAX",
    "ContextBuilder",
    "ContextOverflowError",
    "HistoryManager",
    "Message",
    "MessageRole",
    "estimate_tokens",
]
