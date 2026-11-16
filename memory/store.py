"""
ChromaDB 情景记忆存储模块，负责存储长期记忆。

- 只用一个 collection：``episodic``，一条记录对应一个会话（``session_id``）。
- 会话摘要在同一 ``session_id`` 上 **upsert（合并更新）**：反复压缩只更新同一条
  记录，不会随轮次无限追加。
- ChromaDB 的 PersistentClient 底层用 SQLite 落盘，默认目录 ``memory_db/``。
- 默认 embedder 是离线可用的字符哈希向量；需要真正的语义检索时，构造时传入
  ``embedder``（例如 BGE / sentence-transformers 的编码函数）。换 embedder 后
  向量维度会变，需要清空 ``memory_db/``。
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

# chromadb 1.x 自带的 posthog 版本不兼容时会打印无害的 telemetry 报错，这里静音。
logging.getLogger("chromadb.telemetry.product.posthog").disabled = True

HISTORY_TOP_K = 5          # 情景记忆检索返回条数
EMBEDDING_DIM = 384        # 默认哈希向量维度
DEFAULT_DB_DIR = Path(__file__).resolve().parents[1] / "memory_db"
COLLECTION_NAME = "episodic"

Embedder = Callable[[str], Sequence[float]]


def hash_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """离线可用的字符 bigram 哈希向量（L2 归一化）。

    不依赖模型下载，中英文都能按字面重合度召回；它是默认实现，不是最优的
    语义向量。接入真实向量模型时只需传入同签名的 ``embedder``。
    """
    compact = "".join(str(text or "").lower().split())
    vector = [0.0] * dim
    if not compact:
        vector[0] = 1.0
        return vector

    grams = [compact[i:i + 2] for i in range(len(compact) - 1)] or [compact]
    for gram in grams:
        digest = hashlib.md5(gram.encode("utf-8")).digest()
        vector[int.from_bytes(digest[:4], "big") % dim] += 1.0

    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


@dataclass(frozen=True)
class Episode:
    """一条情景记忆（一个会话的合并摘要）。"""

    session_id: str
    user_id: str
    summary: str
    updated_at: str = ""
    message_count: int = 0
    score: float | None = None

    def render(self) -> str:
        """渲染为一行，供 ContextBuilder 拼进模型上下文。"""
        when = f"（{self.updated_at}）" if self.updated_at else ""
        return f"- {self.summary}{when}"


class MemoryStore:
    """情景记忆存储：按 ``user_id`` 隔离、按 ``session_id`` 合并更新。"""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        collection_name: str = COLLECTION_NAME,
        embedder: Embedder | None = None,
        client: Any = None,
    ) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB_DIR
        self.collection_name = collection_name
        self.embedder = embedder or hash_embedding
        self._client = client
        self._collection: Any = None

    # ---- 基础设施 ----
    def _get_collection(self) -> Any:
        """延迟创建 ChromaDB client / collection，避免 import 时就落盘。"""
        if self._collection is None:
            if self._client is None:
                import chromadb
                from chromadb.config import Settings

                self.path.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=str(self.path),
                    settings=Settings(anonymized_telemetry=False),
                )
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                configuration={"hnsw": {"space": "cosine"}},
            )
        return self._collection

    def _embed(self, text: str) -> list[float]:
        return [float(value) for value in self.embedder(text)]

    # ---- 写入 ----
    def upsert(
        self,
        *,
        session_id: str,
        user_id: str,
        summary: str,
        message_count: int = 0,
    ) -> None:
        """合并更新某会话的摘要（同 id upsert，不新增记录）。"""
        session_id = (session_id or "").strip()
        user_id = (user_id or "").strip()
        summary = (summary or "").strip()
        if not session_id or not user_id or not summary:
            raise ValueError("session_id / user_id / summary 不能为空")

        metadata = {
            "session_id": session_id,
            "user_id": user_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "message_count": int(message_count),
        }
        self._get_collection().upsert(
            ids=[session_id],
            documents=[summary],
            embeddings=[self._embed(summary)],
            metadatas=[metadata],
        )

    # ---- 读取 ----
    def get(self, session_id: str) -> Episode | None:
        """读取某会话已有的情景记忆。"""
        session_id = (session_id or "").strip()
        if not session_id:
            return None
        result = self._get_collection().get(
            ids=[session_id],
            include=["documents", "metadatas"],
        )
        ids = result.get("ids") or []
        if not ids:
            return None
        documents = result.get("documents") or [""]
        metadatas = result.get("metadatas") or [{}]
        return self._to_episode(ids[0], documents[0], metadatas[0])

    def search(
        self,
        query: str,
        *,
        user_id: str,
        top_k: int = HISTORY_TOP_K,
        exclude_session: str | None = None,
    ) -> list[Episode]:
        """语义检索同一用户的历史会话摘要。

        Args:
            query: 当前用户输入
            user_id: 画像/记忆隔离键
            top_k: 返回条数
            exclude_session: 当前会话 id（其摘要已在工作记忆里，避免重复）
        """
        query = (query or "").strip()
        user_id = (user_id or "").strip()
        if not query or not user_id or top_k <= 0:
            return []

        collection = self._get_collection()
        if collection.count() == 0:
            return []

        where: dict[str, Any] = {"user_id": user_id}
        if exclude_session:
            where = {
                "$and": [
                    {"user_id": user_id},
                    {"session_id": {"$ne": exclude_session}},
                ]
            }

        result = collection.query(
            query_embeddings=[self._embed(query)],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        episodes: list[Episode] = []
        for index, episode_id in enumerate(ids):
            document = documents[index] if index < len(documents) else ""
            metadata = metadatas[index] if index < len(metadatas) else {}
            distance = distances[index] if index < len(distances) else None
            score = round(1.0 - float(distance), 4) if distance is not None else None
            episodes.append(self._to_episode(episode_id, document, metadata, score))
        return episodes

    def count(self) -> int:
        """当前 collection 的记录数。"""
        return int(self._get_collection().count())

    # ---- 清理 ----
    def delete(self, session_id: str) -> None:
        """删除某会话的情景记忆（用于会话清理/测试）。"""
        session_id = (session_id or "").strip()
        if session_id:
            self._get_collection().delete(ids=[session_id])

    @staticmethod
    def _to_episode(
        session_id: str,
        document: str | None,
        metadata: dict[str, Any] | None,
        score: float | None = None,
    ) -> Episode:
        metadata = metadata or {}
        return Episode(
            session_id=str(metadata.get("session_id") or session_id),
            user_id=str(metadata.get("user_id") or ""),
            summary=str(document or ""),
            updated_at=str(metadata.get("updated_at") or ""),
            message_count=int(metadata.get("message_count") or 0),
            score=score,
        )


__all__ = [
    "DEFAULT_DB_DIR",
    "EMBEDDING_DIM",
    "Episode",
    "HISTORY_TOP_K",
    "MemoryStore",
    "hash_embedding",
]
