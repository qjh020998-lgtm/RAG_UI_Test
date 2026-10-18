"""RAG 知识库文档加载器。

统一入口：读取 docs/ 目录下所有文档（MD + PDF），
执行切分、OCR 识别（带缓存）、插件章节过滤，
输出结构化 JSON 文件供后续 Embedding 使用。

用法:
    python agent/load_documents.py
    python agent/load_documents.py --docs-dir D:/custom/path
    python agent/load_documents.py --output chunks.json
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import List

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.document import DocumentProcessor
from tools.document.schema import ChunkType, DocumentChunk


def load_documents(
    docs_dir: Path,
    image_output_dir: Path,
    output_path: Path,
) -> List[DocumentChunk]:
    """加载并切分所有文档，返回 chunk 列表。"""
    print(f"[信息] 文档目录: {docs_dir}")
    print(f"[信息] 图片输出: {image_output_dir}")
    print(f"[信息] JSON 输出: {output_path}")
    print()

    processor = DocumentProcessor(image_output_dir=image_output_dir)

    print("=" * 70)
    print("开始处理文档...")
    print("=" * 70)

    t0 = time.perf_counter()
    all_chunks = processor.process_directory(docs_dir)
    elapsed = time.perf_counter() - t0

    print(f"\n[完成] 处理完成，耗时 {elapsed:.1f}s")
    return all_chunks


def generate_statistics(all_chunks: List[DocumentChunk]) -> dict:
    """生成切分统计信息。"""
    doc_stats = defaultdict(lambda: {"text": 0, "image": 0, "table": 0, "total": 0})
    type_dist = defaultdict(int)
    module_dist = defaultdict(int)

    for chunk in all_chunks:
        doc = chunk.metadata.source_doc
        ct = chunk.metadata.chunk_type.value
        module = chunk.metadata.module or "未分类"
        doc_stats[doc][ct] += 1
        doc_stats[doc]["total"] += 1
        type_dist[ct] += 1
        module_dist[module] += 1

    return {
        "total_chunks": len(all_chunks),
        "doc_stats": {k: dict(v) for k, v in doc_stats.items()},
        "type_distribution": dict(type_dist),
        "module_distribution": dict(module_dist),
    }


def print_statistics(stats: dict, all_chunks: List[DocumentChunk]) -> None:
    """打印统计信息到控制台。"""
    print()
    print("=" * 70)
    print("文档切分结果汇总")
    print("=" * 70)
    total = stats["total_chunks"]
    print(f"总 chunk 数: {total}")
    print(f'文档数: {len(stats["doc_stats"])}')

    print()
    print(f'{chr(34)}文档{chr(34):<45} {chr(34)}文本{chr(34):>6} {chr(34)}图片{chr(34):>6} {chr(34)}表格{chr(34):>6} {chr(34)}合计{chr(34):>6}')
    print("-" * 75)
    for doc, ds in sorted(stats["doc_stats"].items()):
        print(f'{doc:<45} {ds["text"]:>6} {ds["image"]:>6} {ds["table"]:>6} {ds["total"]:>6}')

    print("\n模块分布 (Top 20):")
    for mod, cnt in sorted(stats["module_distribution"].items(), key=lambda x: -x[1])[:20]:
        print(f"  {mod}: {cnt}")

    print("\n类型分布:")
    for ct, cnt in sorted(stats["type_distribution"].items()):
        pct = cnt * 100 // total
        print(f"  {ct}: {cnt} ({pct}%)")

    lengths = [len(c.content) for c in all_chunks]
    avg = sum(lengths) // len(lengths)
    print(f"\n内容长度: 最短 {min(lengths)} / 最长 {max(lengths)} / 平均 {avg} 字符")


def export_to_json(
    all_chunks: List[DocumentChunk],
    stats: dict,
    output_path: Path,
) -> None:
    """导出结构化 JSON。"""
    export_data = {
        "metadata": {
            "total_chunks": stats["total_chunks"],
            "doc_stats": stats["doc_stats"],
            "type_distribution": stats["type_distribution"],
            "module_distribution": stats["module_distribution"],
        },
        "chunks": [chunk.model_dump() for chunk in all_chunks],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(export_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    size_kb = output_path.stat().st_size / 1024
    print(f"\n[完成] 已导出: {output_path} ({size_kb:.1f} KB)")


def main():
    parser = argparse.ArgumentParser(description="RAG 知识库文档加载器")
    parser.add_argument(
        "--docs-dir", type=Path, default=PROJECT_ROOT / "docs",
    )
    parser.add_argument(
        "--image-output-dir", type=Path,
        default=PROJECT_ROOT / "knowledge-store" / "images",
    )
    parser.add_argument(
        "--output", type=Path,
        default=PROJECT_ROOT / "cache" / "chunks.json",
    )
    args = parser.parse_args()

    if not args.docs_dir.exists():
        print(f"[错误] 文档目录不存在: {args.docs_dir}")
        sys.exit(1)

    all_chunks = load_documents(
        args.docs_dir, args.image_output_dir, args.output,
    )

    if not all_chunks:
        print("[警告] 未找到任何文档")
        sys.exit(0)

    stats = generate_statistics(all_chunks)
    print_statistics(stats, all_chunks)
    export_to_json(all_chunks, stats, args.output)

    print()
    print("=" * 70)
    print("文档加载完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
