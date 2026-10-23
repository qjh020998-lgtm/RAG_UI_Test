"""Milvus 连接与读写封装。

职责
----
- **连接管理**：延迟连接，``import`` 本模块不会触发网络请求
- **collection 生命周期**：创建 / 检查 / 删除 / 加载
- **数据写入**：把 ``chunk + 向量`` 转成 Milvus 行并插入
- **检索**：dense / sparse 两路查询，带 ``expr`` 标量过滤

不负责
------
- 不负责 embedding（P3.2 的 indexer 负责调 BGE-M3）
- 不负责 chunk 切分（``agent/chunking.py``）

配置来源
--------
优先读环境变量（``.env`` 会被自动加载）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| ``RAG_MILVUS_URI`` | ``http://localhost:19530`` | Milvus 地址 |
| ``RAG_MILVUS_TOKEN`` | ``""`` | 无鉴权时留空 |
| ``RAG_MILVUS_DB_NAME`` | ``default`` | 数据库名 |
| ``RAG_EMBEDDING_DENSE_DIM`` | ``1024`` | 与 BGE-M3 保持一致 |
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pymilvus import Collection, connections, utility

from agent.milvus_schema import (
    COLLECTION_BY_LAYER,
    DENSE_FIELD,
    PRIMARY_KEY,
    SPARSE_FIELD,
    build_row,
    build_schema,
    iter_index_specs,
)

# 项目根 .env（若未安装 python-dotenv 则跳过，不影响显式设置的环境变量）
try:  # pragma: no cover - 取决于运行环境
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover
    pass

log = logging.getLogger(__name__)

DEFAULT_URI = "http://localhost:19530"
DEFAULT_ALIAS = "default"


class MilvusUnavailableError(RuntimeError):
    """Milvus 不可达；异常信息里带排查步骤。"""

    def __init__(self, uri: str, cause: Exception | None = None):
        self.uri = uri
        self.cause = cause
        super().__init__(
            f"无法连接 Milvus（{uri}）。请依次确认：\n"
            f"  1) docker compose up -d\n"
            f"  2) docker compose ps  # etcd / minio / milvus 三个容器应为 healthy\n"
            f"  3) .env 中 RAG_MILVUS_URI 是否正确（当前 {uri}）\n"
            f"原始错误：{cause!r}"
        )


@dataclass(frozen=True)
class MilvusSettings:
    """连接参数。"""

    uri: str = DEFAULT_URI
    token: str = ""
    db_name: str = "default"
    alias: str = DEFAULT_ALIAS
    dense_dim: int = 1024
    collections: dict[str, str] = field(default_factory=lambda: dict(COLLECTION_BY_LAYER))

    @classmethod
    def from_env(cls) -> "MilvusSettings":
        return cls(
            uri=os.getenv("RAG_MILVUS_URI", DEFAULT_URI),
            token=os.getenv("RAG_MILVUS_TOKEN", ""),
            db_name=os.getenv("RAG_MILVUS_DB_NAME", "default"),
            alias=DEFAULT_ALIAS,
            dense_dim=int(os.getenv("RAG_EMBEDDING_DENSE_DIM", "1024")),
            collections={
                "code": os.getenv("RAG_MILVUS_CODE_COLLECTION", COLLECTION_BY_LAYER["code"]),
                "test": os.getenv("RAG_MILVUS_TEST_COLLECTION", COLLECTION_BY_LAYER["test"]),
                "knowledge": os.getenv(
                    "RAG_MILVUS_KNOWLEDGE_COLLECTION", COLLECTION_BY_LAYER["knowledge"]
                ),
            },
        )


class MilvusStore:
    """Milvus 的轻量封装：延迟连接 + collection 管理 + 读写。"""

    def __init__(self, settings: MilvusSettings | None = None):
        self.settings = settings or MilvusSettings.from_env()
        self._connected = False

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """建立连接；失败抛 :class:`MilvusUnavailableError`。"""
        if self._connected:
            return
        try:
            connections.connect(
                alias=self.settings.alias,
                uri=self.settings.uri,
                token=self.settings.token or None,
                db_name=self.settings.db_name,
            )
        except Exception as exc:  # noqa: BLE001 - 统一包装成带提示的异常
            self._connected = False
            raise MilvusUnavailableError(self.settings.uri, exc) from exc

        self._connected = True
        log.info("Milvus 已连接：%s", self.settings.uri)

    def disconnect(self) -> None:
        if not self._connected:
            return
        try:
            connections.disconnect(self.settings.alias)
        finally:
            self._connected = False

    def is_healthy(self) -> bool:
        """不抛异常的健康检查，供 CLI/测试使用。"""
        try:
            self.connect()
            utility.get_server_version(using=self.settings.alias)
            return True
        except Exception:  # noqa: BLE001
            return False

    def __enter__(self) -> "MilvusStore":
        self.connect()
        return self

    def __exit__(self, *_: Any) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # collection
    # ------------------------------------------------------------------

    def has_collection(self, name: str) -> bool:
        self.connect()
        return utility.has_collection(name, using=self.settings.alias)

    def ensure_collection(self, name: str, dense_dim: int | None = None) -> Collection:
        """返回 collection；不存在则按 schema 创建并建索引、加载。"""
        self.connect()
        if utility.has_collection(name, using=self.settings.alias):
            return Collection(name, using=self.settings.alias)

        dim = dense_dim or self.settings.dense_dim
        collection = Collection(
            name,
            schema=build_schema(name, dense_dim=dim),
            using=self.settings.alias,
        )
        for field_name, index_params in iter_index_specs():
            collection.create_index(field_name=field_name, index_params=index_params)
        collection.load()
        log.info("已创建 collection %s（layer=%s, dense_dim=%s）", name, self._layer_of(name), dim)
        return collection

    def _layer_of(self, collection_name: str) -> str:
        for layer, name in self.settings.collections.items():
            if name == collection_name:
                return layer
        return "?"

    def drop_collection(self, name: str) -> None:
        self.connect()
        if utility.has_collection(name, using=self.settings.alias):
            utility.drop_collection(name, using=self.settings.alias)
            log.info("已删除 collection %s", name)

    def ensure_all(self, dense_dim: int | None = None) -> dict[str, Collection]:
        """一次性保证三个 collection 都存在。"""
        return {
            layer: self.ensure_collection(name, dense_dim=dense_dim)
            for layer, name in self.settings.collections.items()
        }

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def insert(
        self,
        collection_name: str,
        rows: list[dict],
        *,
        flush: bool = True,
    ) -> dict:
        """批量插入。

        ``rows`` 的键必须是 Milvus 字段名；用 :meth:`make_row` 生成。
        """
        if not rows:
            return {"insert_count": 0, "primary_keys": []}

        collection = self.ensure_collection(collection_name)
        result = collection.insert(rows)
        if flush:
            collection.flush()
        return {
            "insert_count": int(result.insert_count),
            "primary_keys": list(result.primary_keys or []),
        }

    def make_row(
        self,
        chunk_id: str,
        metadata: dict,
        content: str,
        dense: list[float],
        sparse: dict[int, float],
    ) -> dict:
        """把 ``chunk`` 的字段转成 Milvus 行（``class`` -> ``class_name`` 等）。"""
        return build_row(chunk_id, metadata, content, dense, sparse)

    def delete_by(self, collection_name: str, expr: str) -> int:
        """按表达式删除，返回删除条数。"""
        collection = self.ensure_collection(collection_name)
        result = collection.delete(expr)
        return int(getattr(result, "delete_count", 0) or 0)

    def replace_layer(
        self,
        collection_name: str,
        layer: str,
        rows: list[dict],
        *,
        flush: bool = True,
    ) -> dict:
        """幂等重建一层：先按 ``layer`` 删除，再插入。"""
        deleted = self.delete_by(collection_name, f'layer == "{layer}"')
        inserted = self.insert(collection_name, rows, flush=flush)
        return {"deleted": deleted, **inserted}

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def count(self, collection_name: str) -> int:
        """逻辑行数（``count(*)``，不含 tombstone）。"""
        collection = self.ensure_collection(collection_name)
        try:
            rows = collection.query(
                expr=f'{PRIMARY_KEY} != ""',
                output_fields=["count(*)"],
            )
            return int(rows[0].get("count(*)", 0)) if rows else 0
        except Exception:  # noqa: BLE001 - 旧版本不支持 count(*) 时回退
            return int(collection.num_entities)

    def search(
        self,
        collection_name: str,
        data: list,
        *,
        anns_field: str = DENSE_FIELD,
        limit: int = 5,
        expr: str | None = None,
        output_fields: list[str] | None = None,
        search_params: dict | None = None,
    ) -> list:
        """向量检索。

        - ``anns_field="dense"``：``data`` 是 ``[[float, ...]]``
        - ``anns_field="sparse"``：``data`` 是 ``[{token_id: weight}]``
        """
        collection = self.ensure_collection(collection_name)
        params = search_params or _default_search_params(anns_field)
        return collection.search(
            data=data,
            anns_field=anns_field,
            param=params,
            limit=limit,
            expr=expr,
            output_fields=output_fields or ["*"],
        )

    def query(
        self,
        collection_name: str,
        expr: str,
        *,
        output_fields: list[str] | None = None,
        limit: int = 100,
    ) -> list:
        """标量查询（不走向量）。"""
        collection = self.ensure_collection(collection_name)
        return collection.query(
            expr=expr,
            output_fields=output_fields or ["*"],
            limit=limit,
        )

    def stats(self) -> dict[str, dict]:
        """三层 collection 的存在性与行数。"""
        result: dict[str, dict] = {}
        for layer, name in self.settings.collections.items():
            exists = self.has_collection(name)
            result[layer] = {
                "collection": name,
                "exists": exists,
                "count": self.count(name) if exists else 0,
            }
        return result


def _default_search_params(anns_field: str) -> dict:
    if anns_field == DENSE_FIELD:
        return {"metric_type": "COSINE", "params": {"nprobe": 16}}
    if anns_field == SPARSE_FIELD:
        return {"metric_type": "IP", "params": {}}
    raise ValueError(f"不支持的向量字段: {anns_field}")


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_store: MilvusStore | None = None


def get_store(settings: MilvusSettings | None = None) -> MilvusStore:
    """进程级单例；不会自动连接，首次调用方法时才连。"""
    global _store
    if _store is None:
        _store = MilvusStore(settings)
    return _store


def reset_store() -> None:
    """关闭并清空单例，供测试或配置切换使用。"""
    global _store
    if _store is not None:
        _store.disconnect()
    _store = None
