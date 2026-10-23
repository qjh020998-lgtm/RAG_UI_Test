"""索引构建：把三层 chunk 编码后写入 Milvus。

流水线
------
```
chunk_code / chunk_tests / chunk_knowledge
  → embedder.encode（dense + sparse）
  → milvus_schema.build_row
  → milvus_connect.replace_layer（先按 layer 删除，再批量插入）
  → knowledge-store/manifests/{ts}_index.json
```

设计要点
--------
- **幂等**：每次按 ``layer`` 先删后插；重复索引后 ``count(*)`` 不变。
- **分批**：按 ``batch_size * 4`` 分段编码与插入，避免一次把 521 条全文塞进内存。
- **schema 守卫**：索引前检查 24 个字段是否齐全；旧 collection（如 Phase 2.5 的
  ``erp_knowledge``）会直接报错并提示 ``--rebuild``，不会静默写坏数据。
- **不重新切分 knowledge 的 PDF**：``include_pdf`` 默认 True，但 OCR 有 ``cache/ocr_cache.json``
  缓存；调试时用 ``--no-pdf`` 只索引 MD。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from agent.chunking import (
    BaseChunk,
    chunk_code,
    chunk_knowledge,
    chunk_tests,
)
from agent.embedding import EmbeddingSettings, get_embedder
from agent.milvus_connect import MilvusStore, get_store
from agent.milvus_schema import (
    CONTENT_FIELD,
    DENSE_FIELD,
    METADATA_FIELD_NAMES,
    PRIMARY_KEY,
    SPARSE_FIELD,
    build_row,
)

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIR = PROJECT_ROOT / "knowledge-store" / "manifests"

ALL_LAYERS: tuple[str, ...] = ("code", "test", "knowledge")

_EXPECTED_FIELDS: frozenset[str] = frozenset(
    {PRIMARY_KEY, CONTENT_FIELD, DENSE_FIELD, SPARSE_FIELD, *METADATA_FIELD_NAMES}
)


class SchemaMismatchError(RuntimeError):
    """collection 是旧 schema，缺少新字段。"""


@dataclass
class IndexReport:
    """单层索引结果。"""

    layer: str
    collection: str
    chunks: int
    dense_dim: int
    deleted: int
    inserted: int
    elapsed: float
    manifest: str = ""


def _batches(items: Sequence, size: int) -> Iterable[Sequence]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _assert_schema(store: MilvusStore, collection_name: str) -> None:
    collection = store.ensure_collection(collection_name)
    fields = {field.name for field in collection.schema.fields}
    missing = _EXPECTED_FIELDS - fields
    if missing:
        raise SchemaMismatchError(
            f"{collection_name} 不是当前 schema，缺少 {sorted(missing)}。\n"
            f"该 collection 可能是 Phase 2.5 的旧库；请用 "
            f"`python -m agent.indexer --layers {collection_name} --rebuild` "
            f"重建（会先 drop_collection）。"
        )


def index_layer(
    layer: str,
    chunks: Sequence[BaseChunk],
    *,
    collection: str | None = None,
    embedder=None,
    store: MilvusStore | None = None,
    batch_size: int | None = None,
    rebuild: bool = False,
    limit: int = 0,
) -> IndexReport:
    """索引单层。

    Args:
        layer: ``code`` / ``test`` / ``knowledge``
        chunks: 已切分的 chunk 列表
        collection: 覆盖默认 collection 名（测试用独立库）
        rebuild: 先 drop collection 再建（旧 schema 迁移用）
        limit: 只索引前 N 条（调试用）
    """
    store = store or get_store()
    embedder = embedder or get_embedder()
    collection_name = collection or store.settings.collections[layer]
    settings: EmbeddingSettings = embedder.settings
    batch = batch_size or settings.batch_size

    if limit:
        chunks = list(chunks)[:limit]

    if rebuild:
        store.drop_collection(collection_name)
    store.ensure_collection(collection_name, dense_dim=settings.dense_dim)
    _assert_schema(store, collection_name)

    started = time.perf_counter()
    deleted = store.delete_by(collection_name, f'layer == "{layer}"')
    inserted = 0

    for group in _batches(list(chunks), batch * 4):
        texts = [chunk.text for chunk in group]
        dense_list, sparse_list = embedder.encode(texts, batch_size=batch)
        rows = [
            build_row(chunk.chunk_id, chunk.metadata, chunk.text, dense, sparse)
            for chunk, dense, sparse in zip(group, dense_list, sparse_list)
        ]
        result = store.insert(collection_name, rows, flush=False)
        inserted += result["insert_count"]
        log.info("[%s] %s/%s", layer, inserted, len(chunks))

    store.ensure_collection(collection_name).flush()
    elapsed = time.perf_counter() - started

    return IndexReport(
        layer=layer,
        collection=collection_name,
        chunks=len(chunks),
        dense_dim=settings.dense_dim,
        deleted=deleted,
        inserted=inserted,
        elapsed=round(elapsed, 2),
    )


def index_all(
    layers: Sequence[str] = ("code", "test"),
    *,
    include_pdf: bool = True,
    rebuild: bool = False,
    batch_size: int | None = None,
    limit: int = 0,
) -> list[IndexReport]:
    """按层切分并索引，最后写 manifest。"""
    unknown = set(layers) - set(ALL_LAYERS)
    if unknown:
        raise ValueError(f"未知层：{sorted(unknown)}，可选 {ALL_LAYERS}")

    chunks_by_layer: dict[str, list[BaseChunk]] = {}
    if "code" in layers:
        chunks_by_layer["code"] = chunk_code()
    if "test" in layers:
        chunks_by_layer["test"] = chunk_tests()
    if "knowledge" in layers:
        chunks_by_layer["knowledge"] = chunk_knowledge(include_pdf=include_pdf)

    reports: list[IndexReport] = []
    for layer in layers:
        chunks = chunks_by_layer.get(layer, [])
        print(f"\n[{layer}] 切出 {len(chunks)} 条，开始索引…")
        report = index_layer(
            layer,
            chunks,
            rebuild=rebuild,
            batch_size=batch_size,
            limit=limit,
        )
        reports.append(report)
        print(
            f"[{layer}] 完成：deleted={report.deleted} inserted={report.inserted} "
            f"count={_safe_count(report)} elapsed={report.elapsed}s"
        )

    manifest = write_manifest(reports)
    print(f"\nmanifest: {manifest.relative_to(PROJECT_ROOT)}")
    return reports


def _safe_count(report: IndexReport) -> int:
    try:
        return get_store().count(report.collection)
    except Exception:  # noqa: BLE001
        return -1


def write_manifest(
    reports: Sequence[IndexReport],
    *,
    out_dir: Path = MANIFEST_DIR,
) -> Path:
    """写本次索引的 manifest，便于回溯。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = out_dir / f"{now:%Y%m%dT%H%M%S}_index.json"
    payload = {
        "timestamp": now.isoformat(),
        "total_chunks": sum(report.chunks for report in reports),
        "total_inserted": sum(report.inserted for report in reports),
        "layers": [asdict(report) for report in reports],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def print_stats(store: MilvusStore | None = None) -> None:
    store = store or get_store()
    stats = store.stats()
    print("Milvus collection 状态：")
    for layer, item in stats.items():
        flag = "OK" if item["exists"] else "MISSING"
        print(f"  [{flag:7}] {layer:10} {item['collection']:16} count={item['count']}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="三层 chunk 索引构建")
    parser.add_argument(
        "--layers",
        default="code,test",
        help="逗号分隔，可选 code,test,knowledge（默认 code,test）",
    )
    parser.add_argument("--all", action="store_true", help="等价于 --layers code,test,knowledge")
    parser.add_argument("--rebuild", action="store_true", help="先 drop collection 再建（旧 schema 迁移）")
    parser.add_argument("--no-pdf", action="store_true", help="knowledge 跳过 PDF")
    parser.add_argument("--batch-size", type=int, default=0, help="覆盖 .env 的批大小")
    parser.add_argument("--limit", type=int, default=0, help="只索引前 N 条（调试）")
    parser.add_argument("--stats", action="store_true", help="只打印 collection 状态")
    args = parser.parse_args(argv)

    if args.stats:
        print_stats()
        return 0

    layers = ALL_LAYERS if args.all else tuple(
        part.strip() for part in args.layers.split(",") if part.strip()
    )
    reports = index_all(
        layers,
        include_pdf=not args.no_pdf,
        rebuild=args.rebuild,
        batch_size=args.batch_size or None,
        limit=args.limit,
    )
    print()
    print_stats()
    return 0 if all(report.inserted == report.chunks for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
