"""Milvus 数据浏览工具 - 查看 erp_knowledge collection 的实际数据。

用法: python tools/view_milvus_data.py [--type text|image|table] [--module xxx] [--limit N]
"""

import sys
import argparse
import json
from collections import Counter
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pymilvus import Collection, connections

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIR = PROJECT_ROOT / "knowledge-store" / "manifests"


def connect():
    connections.connect(alias="default", uri="http://localhost:19530")
    return Collection("erp_knowledge")


def show_manifest():
    """打印最近一次摄入 manifest 摘要。"""
    print("最近一次摄入 manifest:")
    files = sorted(MANIFEST_DIR.glob("*.json"))
    if not files:
        print("  (无，尚未执行 Phase 2.5 ingest)")
        return
    latest = files[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    print(f"  file:          {latest.name}")
    print(f"  timestamp:     {data.get('timestamp')}")
    print(f"  chunks_file:   {data.get('chunks_file')} (md5={data.get('chunks_file_md5', '')[:8]})")
    print(f"  chunk_count:   {data.get('chunk_count')}")
    print(f"  unique_ids:    {data.get('unique_chunk_ids')}")
    print(f"  type_dist:     {data.get('type_distribution')}")
    print(f"  collection:    {data.get('collection')}")
    print(f"  model:         {data.get('embedding_model')}")
    print(f"  elapsed:       {data.get('elapsed_seconds')}s")
    print()


def show_overview(col):
    print("=" * 70)
    print("erp_knowledge Collection \u6982\u89c8")
    print("=" * 70)
    try:
        count_rows = col.query(expr="chunk_index >= 0", output_fields=["count(*)"])
        logical = int(count_rows[0].get("count(*)", 0)) if count_rows else 0
    except Exception:
        logical = col.num_entities
    print(f"\u903b\u8f91\u8bb0\u5f55\u6570 (count(*)): {logical}")
    print(f"\u7269\u7406\u8bb0\u5f55\u6570 (num_entities, \u542b tombstone): {col.num_entities}")
    print(f"\u5206\u533a: {[p.name for p in col.partitions]}")
    print()

    # chunk_type \u7edf\u8ba1
    print("\u6309 chunk_type \u7edf\u8ba1:")
    for ctype in ["text", "image", "table"]:
        rows = col.query(
            expr=f'chunk_type == "{ctype}"',
            output_fields=["chunk_id"],
            limit=10000,
        )
        print(f"  {ctype}: {len(rows)} \u6761")
    print()

    # module \u7edf\u8ba1
    print("\u6309 module \u7edf\u8ba1:")
    all_rows = col.query(
        expr='chunk_type in ["text","image","table"]',
        output_fields=["module"],
        limit=10000,
    )
    module_counts = Counter(r["module"] for r in all_rows)
    for m, c in module_counts.most_common():
        label = m if m else "(\u7a7a)"
        print(f"  {label}: {c} \u6761")
    print()
    show_manifest()


def show_samples(col, chunk_type, module, limit):
    print()
    print("=" * 70)
    title_parts = [f"\u6837\u672c\u6570\u636e ({chunk_type})"]
    if module:
        title_parts.append(f"module={module}")
    title_parts.append(f"\u524d {limit} \u6761")
    print(" | ".join(title_parts))
    print("=" * 70)

    expr_parts = [f'chunk_type == "{chunk_type}"']
    if module:
        expr_parts.append(f'module == "{module}"')
    expr = " and ".join(expr_parts)

    output_fields = [
        "chunk_id", "content", "content_hash", "module", "section_title",
        "source_doc", "page", "chunk_index", "is_plugin",
    ]

    rows = col.query(expr=expr, output_fields=output_fields, limit=limit)

    if not rows:
        print("  (\u65e0\u6570\u636e)")
        return

    for i, r in enumerate(rows):
        print(f"\n--- \u8bb0\u5f55 #{i+1} ---")
        print(f"  chunk_id:      {r['chunk_id']}")
        print(f"  content_hash:  {r.get('content_hash', '')}")
        print(f"  module:        {r['module']}")
        print(f"  section_title: {r['section_title']}")
        print(f"  source_doc:    {r['source_doc']}")
        print(f"  page:          {r['page']}")
        print(f"  chunk_index:   {r['chunk_index']}")
        print(f"  is_plugin:     {r['is_plugin']}")
        content = r["content"][:300].replace("\n", " ")
        if len(r["content"]) > 300:
            content += "..."
        print(f"  content ({len(r['content'])} chars):")
        print(f"    {content}")


def search_demo(col, query_text, limit=5):
    """演示 Phase 2.5 混合检索（dense + sparse + RRF + dense 门控）。"""
    print()
    print("=" * 70)
    print(f"向量搜索演示: '{query_text}' (Top-{limit})")
    print("=" * 70)

    from agent.config import get_rag_config
    from agent.embedding import get_embedder, hybrid_search

    config = get_rag_config()
    results = hybrid_search(
        query_text,
        collection=col,
        config=config,
        embedder=get_embedder(config),
        top_k=limit,
    )

    if not results:
        print("  (dense cosine 低于阈值，判定为无有效召回)")
        return

    for i, item in enumerate(results):
        content = item.content[:200].replace("\n", " ")
        if len(item.content) > 200:
            content += "..."
        print(
            f"\n  #{i+1} rrf={item.score:.4f} dense={item.dense_score:.4f} "
            f"type={item.chunk_type} module={item.module}"
        )
        print(f"      {item.source_doc} | {item.section_title} | p.{item.page}")
        print(f"      {content}")


def main():
    parser = argparse.ArgumentParser(description="Milvus \u6570\u636e\u6d4f\u89c8\u5de5\u5177")
    parser.add_argument("--type", default="text", choices=["text", "image", "table"], help="chunk \u7c7b\u578b")
    parser.add_argument("--module", default="", help="\u6309 module \u8fc7\u6ee4")
    parser.add_argument("--limit", type=int, default=5, help="\u663e\u793a\u6761\u6570")
    parser.add_argument("--search", default="", help="\u641c\u7d22\u67e5\u8be2\u6587\u672c")
    parser.add_argument("--overview", action="store_true", default=True, help="\u663e\u793a\u6982\u89c8")
    args = parser.parse_args()

    col = connect()
    col.load()

    if args.overview:
        show_overview(col)

    if args.search:
        search_demo(col, args.search, limit=args.limit)
    else:
        show_samples(col, args.type, args.module, args.limit)


if __name__ == "__main__":
    main()
