"""BGE-M3 向量嵌入与 Milvus 混合检索模块。

核心功能：
1. BGE3Embedding: 封装 FlagEmbedding BGEM3FlagModel，提供 encode 接口
2. build_embedding_text: 按 chunk_type 构造嵌入文本
3. ingest: 从 cache/_all_chunks.json 读取 → 嵌入 → 写入 Milvus
4. hybrid_search: dense + sparse 双路召回 + RRFRanker 融合检索
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np
from pymilvus import AnnSearchRequest, Collection, RRFRanker

from agent.config import RAGConfig, get_rag_config
from agent.schema import ChunkRecord, ensure_collection

logger = logging.getLogger(__name__)


# ── 嵌入文本构造 ────────────────────────────────────────────────────

def build_embedding_text(chunk: dict[str, Any]) -> str:
    """根据 chunk_type 构造用于嵌入的文本。

    不同 chunk 类型的构造策略：
    - text: 直接使用 content（已含上下文前缀）
    - image: caption + related_text + ocr_text（多源信息融合）
    - table: content 直接使用（Markdown 表格，BGE-M3 理解良好）
    """
    chunk_type = chunk["metadata"]["chunk_type"]

    if chunk_type in ("text", "table"):
        return chunk["content"]

    if chunk_type == "image":
        img_meta = chunk.get("image_metadata") or {}
        parts: list[str] = []

        caption = img_meta.get("caption", "")
        if caption:
            parts.append(caption)

        related_text = img_meta.get("related_text", "")
        if related_text:
            parts.append(related_text)

        ocr_text = img_meta.get("ocr_text", "")
        if ocr_text:
            parts.append(f"[OCR] {ocr_text}")

        if not parts:
            return chunk["content"]

        return "\n".join(parts)

    return chunk["content"]


def _convert_sparse_keys(sparse: dict) -> dict[int, float]:
    """将 BGE-M3 输出的 sparse 向量 key 从 str 转为 int。

    BGE-M3 FlagEmbedding 返回 dict[str, np.float32]，
    Milvus SPARSE_FLOAT_VECTOR 需要 dict[int, float]。
    """
    return {int(k): float(v) for k, v in sparse.items()}


# ── BGE-M3 嵌入模型封装 ────────────────────────────────────────────

class BGE3Embedding:
    """BGE-M3 嵌入模型封装，支持 dense + sparse 双路输出。"""

    def __init__(self, config: RAGConfig | None = None):
        if config is None:
            config = get_rag_config()

        self._config = config.embedding
        self._model = None

    def _ensure_model(self):
        """延迟加载模型（首次调用时初始化）。"""
        if self._model is not None:
            return

        from FlagEmbedding import BGEM3FlagModel

        logger.info("Loading BGE-M3 model from: %s", self._config.model_path)
        self._model = BGEM3FlagModel(
            model_name_or_path=self._config.model_path,
            use_fp16=self._config.use_fp16,
            device=self._config.device,
        )
        logger.info("BGE-M3 model loaded (device=%s, fp16=%s)",
                     self._config.device, self._config.use_fp16)

    def encode(
        self,
        texts: list[str],
        return_dense: bool = True,
        return_sparse: bool = True,
    ) -> dict[str, Any]:
        """批量编码文本为 dense + sparse 向量。

        Returns:
            dict with keys:
            - "dense_vecs": np.ndarray (N, 1024) float32
            - "lexical_weights": list[dict[str, float]] (BGE-M3 原始输出，key 为 str)
        """
        self._ensure_model()

        output = self._model.encode(
            texts,
            batch_size=self._config.batch_size,
            max_length=self._config.max_length,
            return_dense=return_dense,
            return_sparse=return_sparse,
            return_colbert_vecs=False,
        )

        return output

    def encode_query(self, query: str) -> dict[str, Any]:
        """编码单条查询文本。"""
        return self.encode([query])


# ── 数据摄入 ────────────────────────────────────────────────────────

def load_chunks_from_file(chunks_file: str) -> list[dict[str, Any]]:
    """从 cache/_all_chunks.json 加载全量 chunks。"""
    path = Path(chunks_file)
    if not path.exists():
        raise FileNotFoundError(f"Chunks file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    chunks = data.get("chunks", [])
    logger.info("Loaded %d chunks from %s", len(chunks), path.name)
    return chunks


def ingest(
    chunks_file: str | None = None,
    config: RAGConfig | None = None,
) -> int:
    """将 chunks 嵌入并写入 Milvus collection。"""
    if config is None:
        config = get_rag_config()

    if chunks_file is None:
        chunks_file = config.chunks_file

    raw_chunks = load_chunks_from_file(chunks_file)
    if not raw_chunks:
        logger.warning("No chunks found, skipping ingest.")
        return 0

    embedding_texts = [build_embedding_text(c) for c in raw_chunks]
    logger.info("Built embedding text for %d chunks", len(embedding_texts))

    embedder = BGE3Embedding(config)
    output = embedder.encode(embedding_texts)

    dense_vecs = output["dense_vecs"]
    sparse_weights = output["lexical_weights"]

    logger.info("Embedding complete: %d dense vectors, %d sparse vectors",
                len(dense_vecs), len(sparse_weights))

    records: list[ChunkRecord] = []
    for i, raw in enumerate(raw_chunks):
        meta = raw["metadata"]
        record = ChunkRecord(
            chunk_id=raw["chunk_id"],
            embedding_text=embedding_texts[i],
            content=raw["content"],
            source_doc=meta.get("source_doc", ""),
            doc_title=meta.get("doc_title", ""),
            module=meta.get("module", ""),
            section_title=meta.get("section_title", ""),
            page=meta.get("page", 0),
            chunk_type=meta.get("chunk_type", "text"),
            chunk_index=meta.get("chunk_index", 0),
            is_plugin=meta.get("is_plugin", False),
            dense_vector=dense_vecs[i].tolist(),
            sparse_vector=_convert_sparse_keys(sparse_weights[i]),
        )
        records.append(record)

    collection = ensure_collection(config)
    rows = [r.to_milvus_row() for r in records]

    batch_size = 100
    total_inserted = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        result = collection.insert(batch)
        total_inserted += result.insert_count
        logger.info("Inserted batch [%d:%d], count=%d",
                     start, start + len(batch), result.insert_count)

    collection.flush()
    logger.info("Ingest complete: %d chunks written to Milvus [%s]",
                total_inserted, config.milvus.collection_name)

    return total_inserted


# ── 混合检索（dense + sparse + RRF 融合） ──────────────────────────

class SearchResultItem:
    """单条检索结果。"""

    def __init__(
        self,
        chunk_id: str,
        score: float,
        content: str,
        source_doc: str,
        doc_title: str,
        module: str,
        section_title: str,
        chunk_type: str,
        page: int,
    ):
        self.chunk_id = chunk_id
        self.score = score
        self.content = content
        self.source_doc = source_doc
        self.doc_title = doc_title
        self.module = module
        self.section_title = section_title
        self.chunk_type = chunk_type
        self.page = page

    def __repr__(self) -> str:
        return (
            f"SearchResultItem(score={self.score:.4f}, "
            f"type={self.chunk_type}, module={self.module}, "
            f"id={self.chunk_id})"
        )

    def summary(self, max_content_len: int = 120) -> str:
        """生成可读摘要。"""
        content_preview = self.content[:max_content_len].replace("\n", " ")
        if len(self.content) > max_content_len:
            content_preview += "..."
        return (
            f"[{self.chunk_type}] {self.doc_title} | {self.module} | "
            f"score={self.score:.4f}\n    {content_preview}"
        )


def hybrid_search(
    query: str,
    collection: Collection | None = None,
    config: RAGConfig | None = None,
    filters: dict[str, Any] | None = None,
) -> list[SearchResultItem]:
    """执行 dense + sparse 双路召回 + RRFRanker 融合检索。

    Args:
        query: 查询文本
        collection: Milvus Collection 对象
        config: RAG 配置
        filters: metadata 过滤条件

    Returns:
        list[SearchResultItem] 按相关性排序
    """
    if config is None:
        config = get_rag_config()

    if collection is None:
        collection = ensure_collection(config)

    search_cfg = config.search

    # 1. 编码查询
    embedder = BGE3Embedding(config)
    query_output = embedder.encode_query(query)

    dense_query_vec = query_output["dense_vecs"][0].tolist()
    sparse_query_vec = _convert_sparse_keys(query_output["lexical_weights"][0])

    # 2. 构建过滤表达式
    filter_expr = _build_filter_expr(filters)

    # 3. Dense 检索请求
    dense_search = AnnSearchRequest(
        data=[dense_query_vec],
        anns_field="dense_vector",
        param={
            "metric_type": "COSINE",
            "params": {"nprobe": 16},
        },
        limit=search_cfg.dense_top_k,
        expr=filter_expr,
    )

    # 4. Sparse 检索请求
    sparse_search = AnnSearchRequest(
        data=[sparse_query_vec],
        anns_field="sparse_vector",
        param={
            "metric_type": "IP",
        },
        limit=search_cfg.sparse_top_k,
        expr=filter_expr,
    )

    # 5. RRF 融合检索 (pymilvus 2.5.x API: reqs, rerank, limit)
    ranker = RRFRanker(k=search_cfg.rrf_k)

    output_fields = [
        "content", "source_doc", "doc_title", "module",
        "section_title", "chunk_type", "page",
    ]

    results = collection.hybrid_search(
        reqs=[dense_search, sparse_search],
        rerank=ranker,
        limit=search_cfg.top_k,
        output_fields=output_fields,
    )

    # 6. 解析结果
    items: list[SearchResultItem] = []
    if results and len(results) > 0:
        for hits in results:
            for hit in hits:
                entity = hit.entity
                item = SearchResultItem(
                    chunk_id=hit.id,
                    score=hit.score,
                    content=entity.content,
                    source_doc=entity.source_doc,
                    doc_title=entity.doc_title,
                    module=entity.module,
                    section_title=entity.section_title,
                    chunk_type=entity.chunk_type,
                    page=entity.page,
                )
                if item.score >= search_cfg.score_threshold:
                    items.append(item)

    logger.info("Hybrid search for '%s': %d results", query[:50], len(items))
    return items


def _build_filter_expr(filters: dict[str, Any] | None) -> str | None:
    """将过滤字典转换为 Milvus 布尔表达式。"""
    if not filters:
        return None

    string_fields = {"module", "chunk_type", "source_doc", "section_title"}
    bool_fields = {"is_plugin"}

    expr_parts: list[str] = []
    for key, value in filters.items():
        if key in string_fields:
            expr_parts.append(f'{key} == "{value}"')
        elif key in bool_fields:
            expr_parts.append(f'{key} == {str(value).lower()}')
        else:
            logger.warning("Unknown filter field: %s, skipping", key)

    return " and ".join(expr_parts) if expr_parts else None
