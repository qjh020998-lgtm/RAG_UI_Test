"""Milvus Collection Schema 与数据模型定义。

定义 erp_knowledge collection 的字段结构、索引配置，
以及 ChunkRecord（写入 Milvus 的行数据）Pydantic 模型。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    Function,
    FunctionType,
    connections,
    utility,
)

from agent.config import RAGConfig, get_rag_config


# ── Collection 字段常量 ──────────────────────────────────────────────

# VARCHAR 主键字段，格式: {doc_slug}_{type}_{pXXX}_{iXXXX}，最长 32 字符
CHUNK_ID_MAX_LEN = 32
CONTENT_HASH_MAX_LEN = 16

# 文本字段
EMBEDDING_TEXT_MAX_LEN = 16384  # 用于嵌入的拼接文本
CONTENT_MAX_LEN = 65535         # 完整 chunk 内容
SOURCE_DOC_MAX_LEN = 256
DOC_TITLE_MAX_LEN = 256
MODULE_MAX_LEN = 128
SECTION_TITLE_MAX_LEN = 256
CHUNK_TYPE_MAX_LEN = 16


def get_collection_schema() -> CollectionSchema:
    """构建 erp_knowledge collection 的字段定义。

    字段设计说明：
    - chunk_id: 位置型 VARCHAR 主键（{doc_slug}_{type}_{pXXX}_{iXXXX}），ASCII-only、跨次稳定
    - embedding_text: 用于嵌入的文本（按 chunk_type 构造，与 content 不同）
    - content: 原始 chunk 完整内容，用于检索后返回给 LLM
    - content_hash: 内容哈希，仅用于变更检测，不参与主键
    - metadata 字段: source_doc / module / chunk_type 等，用于过滤
    - dense_vector: 1024 维 BGE-M3 dense embedding
    - sparse_vector: BGE-M3 sparse embedding（由 Milvus BM25 函数自动生成时暂不使用，
      改为手动写入 sparse_embedding 字段）
    """
    config = get_rag_config()

    fields = [
        # ── 主键 ──
        FieldSchema(
            name="chunk_id",
            dtype=DataType.VARCHAR,
            max_length=CHUNK_ID_MAX_LEN,
            is_primary=True,
            description="位置型主键: {doc_slug}_{type}_{pXXX}_{iXXXX}",
        ),
        # ── 文本字段 ──
        FieldSchema(
            name="embedding_text",
            dtype=DataType.VARCHAR,
            max_length=EMBEDDING_TEXT_MAX_LEN,
            description="用于嵌入的文本（按 chunk_type 构造）",
        ),
        FieldSchema(
            name="content",
            dtype=DataType.VARCHAR,
            max_length=CONTENT_MAX_LEN,
            description="原始 chunk 完整内容",
        ),
        FieldSchema(
            name="content_hash",
            dtype=DataType.VARCHAR,
            max_length=CONTENT_HASH_MAX_LEN,
            description="md5(content)[:16]，用于变更检测，非主键",
        ),
        # ── 元数据字段（支持 metadata 过滤） ──
        FieldSchema(
            name="source_doc",
            dtype=DataType.VARCHAR,
            max_length=SOURCE_DOC_MAX_LEN,
            description="源文档文件名",
        ),
        FieldSchema(
            name="doc_title",
            dtype=DataType.VARCHAR,
            max_length=DOC_TITLE_MAX_LEN,
            description="文档标题/简称",
        ),
        FieldSchema(
            name="module",
            dtype=DataType.VARCHAR,
            max_length=MODULE_MAX_LEN,
            description="所属业务模块（采购管理/系统管理等）",
        ),
        FieldSchema(
            name="section_title",
            dtype=DataType.VARCHAR,
            max_length=SECTION_TITLE_MAX_LEN,
            description="所属小节标题",
        ),
        FieldSchema(
            name="page",
            dtype=DataType.INT64,
            description="页码（PDF 适用，MD 为 0）",
        ),
        FieldSchema(
            name="chunk_type",
            dtype=DataType.VARCHAR,
            max_length=CHUNK_TYPE_MAX_LEN,
            description="块类型: text / image / table",
        ),
        FieldSchema(
            name="chunk_index",
            dtype=DataType.INT64,
            description="块在文档内的全局序号（0-based）",
        ),
        FieldSchema(
            name="is_plugin",
            dtype=DataType.BOOL,
            description="是否为插件功能",
        ),
        # ── 向量字段 ──
        FieldSchema(
            name="dense_vector",
            dtype=DataType.FLOAT_VECTOR,
            dim=config.embedding.dense_dim,
            description="BGE-M3 dense embedding (1024-d)",
        ),
        FieldSchema(
            name="sparse_vector",
            dtype=DataType.SPARSE_FLOAT_VECTOR,
            description="BGE-M3 sparse embedding (term weights)",
        ),
    ]

    return CollectionSchema(
        fields=fields,
        description="ERP 知识库文档检索 collection",
        enable_dynamic_field=False,
    )


# ── 索引配置 ────────────────────────────────────────────────────────

DENSE_INDEX_PARAMS = {
    "index_type": "IVF_FLAT",
    "metric_type": "COSINE",
    "params": {"nlist": 128},
}

SPARSE_INDEX_PARAMS = {
    "index_type": "SPARSE_INVERTED_INDEX",
    "metric_type": "IP",
}


# ── ChunkRecord: 写入 Milvus 的行数据模型 ────────────────────────────

class ChunkRecord(BaseModel):
    """Milvus 行数据模型，对应 collection 中的一条记录。

    从 cache/chunks.json 中的 DocumentChunk 转换而来，
    增加了 embedding_text 和向量字段。
    """

    chunk_id: str = Field(description="位置型全局唯一标识")
    embedding_text: str = Field(description="用于嵌入的拼接文本")
    content: str = Field(description="原始 chunk 完整内容")
    content_hash: str = Field(default="", description="md5(content)[:16]")
    source_doc: str = Field(description="源文档文件名")
    doc_title: str = Field(description="文档标题")
    module: str = Field(default="", description="业务模块")
    section_title: str = Field(default="", description="小节标题")
    page: int = Field(default=0, description="页码")
    chunk_type: str = Field(description="text / image / table")
    chunk_index: int = Field(default=0, description="块序号")
    is_plugin: bool = Field(default=False, description="是否插件")

    # 向量字段（嵌入后填充）
    dense_vector: Optional[list[float]] = Field(
        default=None, description="1024-d dense embedding"
    )
    sparse_vector: Optional[dict[int, float]] = Field(
        default=None, description="sparse embedding {token_id: weight}"
    )

    def to_milvus_row(self) -> dict[str, Any]:
        """转换为 Milvus insert 所需的字典格式。"""
        row: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "embedding_text": self.embedding_text,
            "content": self.content,
            "content_hash": self.content_hash,
            "source_doc": self.source_doc,
            "doc_title": self.doc_title,
            "module": self.module,
            "section_title": self.section_title,
            "page": self.page,
            "chunk_type": self.chunk_type,
            "chunk_index": self.chunk_index,
            "is_plugin": self.is_plugin,
            "dense_vector": self.dense_vector,
            "sparse_vector": self.sparse_vector,
        }
        return row


# ── Collection 管理工具函数 ──────────────────────────────────────────

def ensure_collection(config: RAGConfig | None = None) -> Collection:
    """确保 Milvus collection 存在（含索引），返回 Collection 对象。

    如果 collection 已存在但缺少索引，会自动创建索引。
    """
    if config is None:
        config = get_rag_config()

    milvus = config.milvus

    # 连接 Milvus
    connections.connect(
        alias="default",
        uri=milvus.uri,
        timeout=milvus.timeout,
    )

    collection_name = milvus.collection_name

    if not utility.has_collection(collection_name):
        schema = get_collection_schema()
        collection = Collection(name=collection_name, schema=schema)
        _create_indexes(collection)
    else:
        collection = Collection(name=collection_name)
        # Phase 2.5: 旧 schema（无 content_hash）不能继续写入，必须显式重建
        existing_fields = {field.name for field in collection.schema.fields}
        if "content_hash" not in existing_fields:
            raise RuntimeError(
                f"Collection '{collection_name}' 是 Phase 2.5 之前的 schema，"
                "缺少 content_hash 字段。请先执行 schema.drop_collection()，"
                "再重新 ingest。"
            )
        # 确保索引存在
        existing_indexes = {idx.field_name for idx in collection.indexes}
        if "dense_vector" not in existing_indexes:
            collection.create_index(
                field_name="dense_vector",
                index_params=DENSE_INDEX_PARAMS,
                index_name="dense_idx",
            )
        if "sparse_vector" not in existing_indexes:
            collection.create_index(
                field_name="sparse_vector",
                index_params=SPARSE_INDEX_PARAMS,
                index_name="sparse_idx",
            )

    # 加载 collection 到内存
    collection.load()
    return collection


def _create_indexes(collection: Collection) -> None:
    """为 collection 创建 dense 和 sparse 索引。"""
    collection.create_index(
        field_name="dense_vector",
        index_params=DENSE_INDEX_PARAMS,
        index_name="dense_idx",
    )
    collection.create_index(
        field_name="sparse_vector",
        index_params=SPARSE_INDEX_PARAMS,
        index_name="sparse_idx",
    )


def drop_collection(config: RAGConfig | None = None) -> None:
    """删除 collection（危险操作，仅用于重建）。"""
    if config is None:
        config = get_rag_config()

    connections.connect(alias="default", uri=config.milvus.uri)
    name = config.milvus.collection_name

    if utility.has_collection(name):
        utility.drop_collection(name)


def list_modules(config: RAGConfig | None = None) -> list[str]:
    """返回库内归一化后的 module 列表（供 Phase 3 自动过滤使用）。"""
    if config is None:
        config = get_rag_config()

    collection = ensure_collection(config)
    rows = collection.query(
        expr="chunk_index >= 0",
        output_fields=["module"],
        limit=16384,
    )
    return sorted({row.get("module", "") for row in rows if row.get("module")})


def get_collection_stats(config: RAGConfig | None = None) -> dict[str, Any]:
    """获取 collection 统计信息（逻辑行数、物理行数、分区等）。

    注意：Milvus 的 ``num_entities`` 在 delete 后、compaction 前仍可能统计
    旧版本（tombstone）；``logical_entities`` 用 ``count(*)`` 得到真实可见行数。
    """
    if config is None:
        config = get_rag_config()

    connections.connect(alias="default", uri=config.milvus.uri)
    collection = Collection(config.milvus.collection_name)
    collection.flush()

    logical = 0
    try:
        rows = collection.query(
            expr="chunk_index >= 0",
            output_fields=["count(*)"],
        )
        if rows:
            logical = int(rows[0].get("count(*)", 0))
    except Exception:
        logical = collection.num_entities

    return {
        "name": config.milvus.collection_name,
        "num_entities": collection.num_entities,
        "logical_entities": logical,
        "partitions": [p.name for p in collection.partitions],
    }
