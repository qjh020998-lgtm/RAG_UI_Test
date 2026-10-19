"""BGE-M3 向量嵌入、Milvus 摄入与混合检索模块。

Phase 2.5 关键改动：
1. ``get_embedder()`` 提供进程级 BGE-M3 单例，避免每次检索冷加载 20s+；
2. ``ingest()`` 增加脏数据守卫，按 ``source_doc`` 先删后 upsert，实现幂等重建；
3. 主键使用位置型 ``chunk_id``，内容哈希降级为 ``content_hash``；
4. ``hybrid_search()`` 附带 dense cosine 分，并用它做相关性门控；
5. 每次摄入写 manifest，记录数据来源、哈希、模型与耗时。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pymilvus import AnnSearchRequest, Collection, RRFRanker

from agent.config import PROJECT_ROOT, RAGConfig, get_rag_config
from agent.schema import (
    CONTENT_MAX_LEN,
    EMBEDDING_TEXT_MAX_LEN,
    ChunkRecord,
    ensure_collection,
)
from tools.document.schema import normalize_module

logger = logging.getLogger(__name__)

# 在 torch / FlagEmbedding 导入前限制 OpenMP 线程数。
# 默认 15 线程时，CPU 前向会因内存峰值直接 abort（Windows 下无 traceback）。
# 4 线程在 15GB 内存机器上可稳定完成 235 chunk 批量嵌入。
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")


# ── 嵌入文本构造 ────────────────────────────────────────────────────

def build_embedding_text(chunk: dict[str, Any]) -> str:
    """根据 chunk_type 构造用于嵌入的文本。

    不同 chunk 类型的构造策略：
    - text: 直接使用 content（已含上下文前缀）
    - image: 文档/模块前缀 + caption + related_text + ocr_text
    - table: content 直接使用（Markdown 表格，BGE-M3 理解良好）
    """
    chunk_type = chunk["metadata"]["chunk_type"]
    meta = chunk["metadata"]

    if chunk_type in ("text", "table"):
        return chunk["content"]

    if chunk_type == "image":
        img_meta = chunk.get("image_metadata") or {}
        parts: list[str] = []

        prefix_parts = [
            meta.get("doc_title", ""),
            meta.get("module", ""),
            meta.get("section_title", ""),
        ]
        prefix = " | ".join(p for p in prefix_parts if p)
        if prefix:
            parts.append(prefix)

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

        import torch

        threads = max(1, int(self._config.num_threads))
        torch.set_num_threads(threads)
        logger.info("PyTorch CPU threads: %d", threads)

        from FlagEmbedding import BGEM3FlagModel

        logger.info("Loading BGE-M3 model from: %s", self._config.model_path)
        self._model = BGEM3FlagModel(
            model_name_or_path=self._config.model_path,
            use_fp16=self._config.use_fp16,
            device=self._config.device,
        )
        logger.info(
            "BGE-M3 model loaded (device=%s, fp16=%s)",
            self._config.device,
            self._config.use_fp16,
        )

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


# ── 进程级 embedder 单例（P1-1） ───────────────────────────────────

_EMBEDDER_LOCK = threading.Lock()
_EMBEDDER_CACHE: dict[tuple[str, str, bool, int, int, int], BGE3Embedding] = {}


def get_embedder(config: RAGConfig | None = None) -> BGE3Embedding:
    """返回进程级 BGE-M3 embedder 单例。

    CPU 下模型冷加载约 20~30s，单条 query 编码约 0.6~1.2s。
    必须在进程内复用，否则每次 hybrid_search 都会退化到 7~8s。
    """
    if config is None:
        config = get_rag_config()

    emb = config.embedding
    key = (
        emb.model_path,
        emb.device,
        emb.use_fp16,
        emb.batch_size,
        emb.max_length,
        emb.num_threads,
    )
    with _EMBEDDER_LOCK:
        embedder = _EMBEDDER_CACHE.get(key)
        if embedder is None:
            embedder = BGE3Embedding(config)
            _EMBEDDER_CACHE[key] = embedder
            logger.info("Created process-level BGE-M3 embedder for %s", emb.device)
        return embedder


# ── 数据摄入 ────────────────────────────────────────────────────────

_DIRTY_MARKERS = (
    "OCR 失败",
    "Traceback (most recent call last)",
    "RUNTIME_EXCEPTION",
)

_CHUNK_ID_RE = re.compile(r"^[a-z0-9]+_(?:txt|img|tbl)_p\d{3,}_i\d{3,}$")


def load_chunks_from_file(chunks_file: str) -> list[dict[str, Any]]:
    """从 cache/chunks.json 加载全量 chunks。"""
    path = Path(chunks_file)
    if not path.exists():
        raise FileNotFoundError(f"Chunks file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    chunks = data.get("chunks", []) if isinstance(data, dict) else data
    if not isinstance(chunks, list):
        raise ValueError(f"Chunks file 格式错误: {path}")

    logger.info("Loaded %d chunks from %s", len(chunks), path.name)
    return chunks


def validate_chunks(
    chunks: list[dict[str, Any]],
    *,
    require_relative_image_paths: bool = True,
) -> list[str]:
    """校验 chunks 是否可安全写入 Milvus。

    返回错误列表；空列表表示通过。任何错误都应阻止 ingest，
    避免污染数据后还要重建整个 collection。
    """
    errors: list[str] = []
    if not chunks:
        return ["chunks 为空"]

    seen_ids: set[str] = set()

    for i, chunk in enumerate(chunks):
        cid = str(chunk.get("chunk_id", ""))
        content = str(chunk.get("content", ""))
        meta = chunk.get("metadata") or {}

        if not cid:
            errors.append(f"[{i}] 缺少 chunk_id")
        elif cid in seen_ids:
            errors.append(f"[{i}] chunk_id 重复: {cid}")
        elif not _CHUNK_ID_RE.match(cid):
            errors.append(
                f"[{i}] chunk_id 不是位置型格式: {cid} "
                "(期望 {slug}_{txt|img|tbl}_pXXX_iXXXX)"
            )
        seen_ids.add(cid)

        if not content.strip():
            errors.append(f"[{i}] {cid} content 为空")

        for marker in _DIRTY_MARKERS:
            if marker in content:
                errors.append(f"[{i}] {cid} content 含脏标记: {marker}")

        if not chunk.get("content_hash"):
            errors.append(f"[{i}] {cid} 缺少 content_hash")

        chunk_type = meta.get("chunk_type")
        if chunk_type not in {"text", "image", "table"}:
            errors.append(f"[{i}] {cid} chunk_type 非法: {chunk_type!r}")

        if chunk_type == "image":
            img_meta = chunk.get("image_metadata") or {}
            image_path = str(img_meta.get("image_path", ""))
            if not image_path:
                errors.append(f"[{i}] {cid} image_path 为空")
            elif Path(image_path).is_absolute():
                errors.append(f"[{i}] {cid} image_path 是绝对路径: {image_path}")
            elif require_relative_image_paths and ".." in Path(image_path).parts:
                errors.append(f"[{i}] {cid} image_path 越出仓库: {image_path}")

    return errors


def _escape_expr(value: str) -> str:
    """转义 Milvus 字符串表达式中的反斜杠和双引号。"""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _relative_to_project(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _write_manifest(
    config: RAGConfig,
    chunks_file: Path,
    records: list[ChunkRecord],
    *,
    elapsed_seconds: float,
    inserted: int,
    deleted_source_docs: list[str],
) -> Path:
    """写摄入 manifest，回答「库内数据由哪次运行产生」。"""
    manifest_dir = Path(config.knowledge_store_dir) / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    type_dist: dict[str, int] = {}
    module_dist: dict[str, int] = {}
    for record in records:
        type_dist[record.chunk_type] = type_dist.get(record.chunk_type, 0) + 1
        module_dist[record.module] = module_dist.get(record.module, 0) + 1

    file_bytes = chunks_file.read_bytes()
    manifest = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "chunks_file": _relative_to_project(chunks_file),
        "chunks_file_md5": hashlib.md5(file_bytes).hexdigest(),
        "chunk_count": len(records),
        "unique_chunk_ids": len({r.chunk_id for r in records}),
        "type_distribution": dict(sorted(type_dist.items())),
        "module_distribution": dict(sorted(module_dist.items())),
        "collection": config.milvus.collection_name,
        "milvus_uri": config.milvus.uri,
        "embedding_model": config.embedding.model_path,
        "embedding_device": config.embedding.device,
        "embedding_dim": config.embedding.dense_dim,
        "inserted": inserted,
        "deleted_source_docs": deleted_source_docs,
        "elapsed_seconds": round(elapsed_seconds, 2),
    }

    path = manifest_dir / f"{ts}.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Ingest manifest written: %s", path)
    return path


def ingest(
    chunks_file: str | None = None,
    config: RAGConfig | None = None,
    *,
    validate_only: bool = False,
    write_manifest: bool = True,
) -> int:
    """将 chunks 嵌入并幂等写入 Milvus collection。

    幂等语义：
    - 先校验全部 chunks，任何脏数据/重复主键/绝对路径都会直接报错；
    - 按 ``source_doc`` 删除旧行，再 upsert 新行；
    - 因此同一份 chunks 连续摄入两次，实体数与 chunk_id 集合保持一致。

    Args:
        chunks_file: chunks JSON 路径（默认 ``cache/chunks.json``）
        config: RAG 配置
        validate_only: 只校验不写库
        write_manifest: 是否写 ``knowledge-store/manifests/{ts}.json``
    """
    started = time.perf_counter()
    if config is None:
        config = get_rag_config()

    if chunks_file is None:
        chunks_file = config.chunks_file

    chunks_path = Path(chunks_file)
    raw_chunks = load_chunks_from_file(str(chunks_path))

    errors = validate_chunks(raw_chunks)
    if errors:
        preview = "\n".join(f"  - {e}" for e in errors[:20])
        more = "" if len(errors) <= 20 else f"\n  ... 还有 {len(errors) - 20} 条"
        raise ValueError(
            f"拒绝摄入：{len(errors)} 个数据质量问题（{chunks_path}）\n"
            f"{preview}{more}"
        )

    if validate_only:
        logger.info("validate_only=True: %d chunks passed validation", len(raw_chunks))
        return 0

    embedding_texts = [build_embedding_text(c) for c in raw_chunks]
    logger.info("Built embedding text for %d chunks", len(embedding_texts))

    embedder = get_embedder(config)
    output = embedder.encode(embedding_texts)

    dense_vecs = output["dense_vecs"]
    sparse_weights = output["lexical_weights"]
    logger.info(
        "Embedding complete: %d dense vectors, %d sparse vectors",
        len(dense_vecs),
        len(sparse_weights),
    )

    records: list[ChunkRecord] = []
    for i, raw in enumerate(raw_chunks):
        meta = raw["metadata"]
        content = str(raw["content"])[:CONTENT_MAX_LEN]
        record = ChunkRecord(
            chunk_id=raw["chunk_id"],
            embedding_text=str(embedding_texts[i])[:EMBEDDING_TEXT_MAX_LEN],
            content=content,
            content_hash=raw.get("content_hash")
            or hashlib.md5(content.encode("utf-8")).hexdigest()[:16],
            source_doc=meta.get("source_doc", ""),
            doc_title=meta.get("doc_title", ""),
            module=normalize_module(meta.get("module", "")),
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

    # 先按 source_doc 删除旧版本，再 upsert；这是幂等重建的关键
    source_docs = sorted({r.source_doc for r in records})
    for source_doc in source_docs:
        collection.delete(expr=f'source_doc == "{_escape_expr(source_doc)}"')
    collection.flush()
    logger.info("Deleted old rows for %d source documents", len(source_docs))

    batch_size = 100
    total_inserted = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        result = collection.upsert(batch)
        count = getattr(result, "upsert_count", None)
        if count is None:
            count = getattr(result, "insert_count", len(batch))
        total_inserted += int(count)
        logger.info(
            "Upserted batch [%d:%d], count=%d",
            start,
            start + len(batch),
            count,
        )

    collection.flush()
    elapsed = time.perf_counter() - started
    logger.info(
        "Ingest complete: %d chunks in %.1fs (collection=%s)",
        total_inserted,
        elapsed,
        config.milvus.collection_name,
    )

    if write_manifest:
        _write_manifest(
            config,
            chunks_path,
            records,
            elapsed_seconds=elapsed,
            inserted=total_inserted,
            deleted_source_docs=source_docs,
        )

    return total_inserted


# ── 混合检索（dense + sparse + RRF 融合 + dense 门控） ──────────────

class SearchResultItem:
    """单条检索结果。

    - ``score``: RRF 融合分（仅用于排序，不能当相关性阈值）；
    - ``dense_score``: dense cosine 分（用于可信度门控）。
    """

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
        *,
        dense_score: float = 0.0,
        content_hash: str = "",
    ):
        self.chunk_id = chunk_id
        self.score = score
        self.dense_score = dense_score
        self.content = content
        self.content_hash = content_hash
        self.source_doc = source_doc
        self.doc_title = doc_title
        self.module = module
        self.section_title = section_title
        self.chunk_type = chunk_type
        self.page = page

    def __repr__(self) -> str:
        return (
            f"SearchResultItem(rrf={self.score:.4f}, dense={self.dense_score:.4f}, "
            f"type={self.chunk_type}, module={self.module}, id={self.chunk_id})"
        )

    def summary(self, max_content_len: int = 120) -> str:
        """生成可读摘要。"""
        content_preview = self.content[:max_content_len].replace("\n", " ")
        if len(self.content) > max_content_len:
            content_preview += "..."
        return (
            f"[{self.chunk_type}] {self.doc_title} | {self.module} | "
            f"rrf={self.score:.4f} dense={self.dense_score:.4f}\n"
            f"    {content_preview}"
        )


def hybrid_search(
    query: str,
    collection: Collection | None = None,
    config: RAGConfig | None = None,
    filters: dict[str, Any] | None = None,
    *,
    embedder: BGE3Embedding | None = None,
    top_k: int | None = None,
    dense_threshold: float | None = None,
) -> list[SearchResultItem]:
    """执行 dense + sparse 双路召回 + RRFRanker 融合检索。

    与 Phase 2 的区别：
    - 复用进程级 embedder；
    - 额外执行一次 dense 搜索拿到 cosine 分，并按它过滤；
    - 按 (source_doc, page, section_title, chunk_type) 去重，避免近重复挤占 Top-K。

    Args:
        query: 查询文本
        collection: Milvus Collection 对象
        config: RAG 配置
        filters: metadata 过滤条件
        embedder: 可选的 embedder（测试可注入）
        top_k: 覆盖配置的最终返回条数
        dense_threshold: 覆盖配置的 dense cosine 阈值

    Returns:
        list[SearchResultItem]，按 RRF 分降序；无有效召回时返回 []
    """
    if config is None:
        config = get_rag_config()

    if collection is None:
        collection = ensure_collection(config)

    search_cfg = config.search
    final_limit = top_k or search_cfg.top_k
    if final_limit <= 0:
        return []

    # 1. 编码查询（进程级单例）
    embedder = embedder or get_embedder(config)
    query_output = embedder.encode_query(query)

    dense_query_vec = query_output["dense_vecs"][0].tolist()
    sparse_query_vec = _convert_sparse_keys(query_output["lexical_weights"][0])

    # 2. 构建过滤表达式
    filter_expr = _build_filter_expr(filters)

    recall_limit = max(search_cfg.dense_top_k, final_limit * 4)
    dense_search_limit = max(
        recall_limit,
        min(int(getattr(collection, "num_entities", 0) or 0), 512),
    )

    # 3. Dense 检索请求
    dense_search = AnnSearchRequest(
        data=[dense_query_vec],
        anns_field="dense_vector",
        param={
            "metric_type": "COSINE",
            "params": {"nprobe": 16},
        },
        limit=recall_limit,
        expr=filter_expr,
    )

    # 4. Sparse 检索请求
    sparse_search = AnnSearchRequest(
        data=[sparse_query_vec],
        anns_field="sparse_vector",
        param={
            "metric_type": "IP",
        },
        limit=recall_limit,
        expr=filter_expr,
    )

    # 5. RRF 融合检索（多召回一些，留给阈值过滤与去重）
    ranker = RRFRanker(k=search_cfg.rrf_k)
    output_fields = [
        "content",
        "content_hash",
        "source_doc",
        "doc_title",
        "module",
        "section_title",
        "chunk_type",
        "page",
    ]

    results = collection.hybrid_search(
        reqs=[dense_search, sparse_search],
        rerank=ranker,
        limit=max(final_limit * 3, 10),
        output_fields=output_fields,
    )

    # 6. 单独取 dense cosine 分（RRF 分没有区分度，不能做门控）
    dense_scores: dict[str, float] = {}
    dense_hits = collection.search(
        data=[dense_query_vec],
        anns_field="dense_vector",
        param={"metric_type": "COSINE", "params": {"nprobe": 16}},
        limit=dense_search_limit,
        expr=filter_expr,
        output_fields=["chunk_id"],
    )
    if dense_hits:
        dense_scores = {str(hit.id): float(hit.score) for hit in dense_hits[0]}

    threshold = (
        search_cfg.score_threshold if dense_threshold is None else dense_threshold
    )

    # 7. 解析、门控、去重
    items: list[SearchResultItem] = []
    seen: set[tuple[str, int, str, str]] = set()

    if results and len(results) > 0:
        for hits in results:
            for hit in hits:
                entity = hit.entity
                dense_score = dense_scores.get(str(hit.id), 0.0)
                if dense_score < threshold:
                    continue

                # text 与 image 同页同节都保留；同类型近重复只保留 RRF 最高的一条
                dedupe_key = (
                    entity.source_doc,
                    int(entity.page),
                    entity.section_title,
                    entity.chunk_type,
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                items.append(
                    SearchResultItem(
                        chunk_id=str(hit.id),
                        score=float(hit.score),
                        dense_score=dense_score,
                        content=entity.content,
                        content_hash=getattr(entity, "content_hash", "") or "",
                        source_doc=entity.source_doc,
                        doc_title=entity.doc_title,
                        module=entity.module,
                        section_title=entity.section_title,
                        chunk_type=entity.chunk_type,
                        page=int(entity.page),
                    )
                )

    items.sort(key=lambda item: (item.score, item.dense_score), reverse=True)
    items = items[:final_limit]

    logger.info(
        "Hybrid search for '%s': %d results (threshold=%.2f, filter=%s)",
        query[:50],
        len(items),
        threshold,
        filters,
    )
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
            expr_parts.append(f'{key} == "{_escape_expr(str(value))}"')
        elif key in bool_fields:
            expr_parts.append(f"{key} == {str(value).lower()}")
        else:
            logger.warning("Unknown filter field: %s, skipping", key)

    return " AND ".join(expr_parts) if expr_parts else None
