"""记忆层：长期记忆（情景记忆）与用户画像，各自独立存储、各自演进。

- 工作记忆在 ``context.history.HistoryManager``（内存）；
- 情景记忆在 :class:`MemoryStore`（ChromaDB ``episodic``）；
- 用户画像在 :class:`UserProfile`（ChromaDB ``user_profile``，按 user_id 稳定存储）。
"""

from .profile import PROFILE_COLLECTION, UserProfile
from .store import HISTORY_TOP_K, Episode, MemoryStore, hash_embedding

__all__ = [
    "HISTORY_TOP_K",
    "PROFILE_COLLECTION",
    "Episode",
    "MemoryStore",
    "UserProfile",
    "hash_embedding",
]
