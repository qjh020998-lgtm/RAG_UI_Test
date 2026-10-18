"""全部文档处理测试。

使用统一的 DocumentProcessor 处理 docs/ 目录下所有文档，
输出综合统计和跨文档检索效果验证。
"""

import json
from collections import defaultdict
from pathlib import Path

import pytest

from tools.document import DocumentProcessor
from tools.document.schema import ChunkType


@pytest.fixture
def processor(tmp_path):
    """创建统一文档处理器。"""
    return DocumentProcessor(image_output_dir=tmp_path / "images")


@pytest.fixture
def docs_dir():
    """文档目录。"""
    return Path(__file__).parent.parent / "docs"


class TestAllDocuments:
    """全部文档综合测试。"""

    def test_process_all_files(self, processor, docs_dir, tmp_path):
        """处理所有文档并输出综合统计。"""
        if not docs_dir.exists():
            pytest.skip(f"目录不存在: {docs_dir}")

        all_chunks = processor.process_directory(docs_dir)

        # ==================== 综合统计 ====================
        print("\n" + "=" * 80)
        print("全部文档切分结果汇总")
        print("=" * 80)
        print(f"总 chunk 数: {len(all_chunks)}")

        # 按文档统计
        doc_stats = defaultdict(lambda: {"text": 0, "image": 0, "table": 0, "total": 0})
        for chunk in all_chunks:
            doc = chunk.metadata.source_doc
            ct = chunk.metadata.chunk_type.value
            doc_stats[doc][ct] += 1
            doc_stats[doc]["total"] += 1

        print(f"\n文档数: {len(doc_stats)}")
        print(f"\n{'文档':<40} {'文本':>6} {'图片':>6} {'表格':>6} {'合计':>6}")
        print("-" * 70)
        for doc, stats in sorted(doc_stats.items()):
            print(
                f"{doc:<40} {stats['text']:>6} {stats['image']:>6} "
                f"{stats['table']:>6} {stats['total']:>6}"
            )

        # 按模块统计（跨文档）
        module_stats = defaultdict(int)
        for chunk in all_chunks:
            module = chunk.metadata.module or "未分类"
            module_stats[module] += 1

        print(f"\n跨文档模块分布 (Top 15):")
        for module, count in sorted(module_stats.items(), key=lambda x: -x[1])[:15]:
            print(f"  {module}: {count}")

        # 类型分布
        type_counts = defaultdict(int)
        for chunk in all_chunks:
            type_counts[chunk.metadata.chunk_type.value] += 1

        print(f"\n类型分布:")
        for ct, count in sorted(type_counts.items()):
            print(f"  {ct}: {count} ({count*100//len(all_chunks)}%)")

        # ==================== 导出 JSON ====================
        output_path = Path(__file__).parent.parent / "cache" / "_all_chunks.json"
        export_data = {
            "total": len(all_chunks),
            "doc_stats": {doc: dict(stats) for doc, stats in doc_stats.items()},
            "type_distribution": dict(type_counts),
            "chunks": [chunk.model_dump() for chunk in all_chunks],
        }
        output_path.write_text(
            json.dumps(export_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n全量导出: {output_path}")


        # ==================== 断言 ====================
        assert len(all_chunks) >= 200, "过滤插件后全部文档应该有 200+ chunks"
        assert len(doc_stats) == 4, "应该处理 4 个文档"
        assert type_counts["text"] >= 80, "过滤插件后应该至少有 80 个文本 chunk"
        assert type_counts["image"] > 70, "过滤插件后应该至少有 70 个图片 chunk"
        assert type_counts["table"] >= 30, "应该至少有 30 个表格 chunk"

    def test_cross_document_module_coverage(self, processor, docs_dir):
        """测试跨文档的模块覆盖度。

        验证同一个业务模块（如"采购管理"）在多个文档中都有覆盖，
        这是 RAG 检索准确性的基础。
        """
        if not docs_dir.exists():
            pytest.skip(f"目录不存在: {docs_dir}")

        all_chunks = processor.process_directory(docs_dir)

        # 按 (模块, 文档) 交叉统计
        cross = defaultdict(set)
        for chunk in all_chunks:
            module = chunk.metadata.module or "未分类"
            cross[module].add(chunk.metadata.source_doc)

        print("\n" + "=" * 80)
        print("跨文档模块覆盖度")
        print("=" * 80)

        multi_doc_modules = {m: docs for m, docs in cross.items() if len(docs) >= 2}
        print(f"出现在 2+ 个文档中的模块: {len(multi_doc_modules)}")

        for module, docs in sorted(multi_doc_modules.items()):
            doc_list = ", ".join(sorted(docs))
            print(f"  {module}: [{doc_list}]")

        # 核心模块应该在多个文档中出现
        core_modules = ["采购", "销售", "零售", "仓库", "系统管理"]
        covered = 0
        for core in core_modules:
            found = [m for m in cross if core in m]
            if found:
                covered += 1
                docs = cross[found[0]]
                print(f"\n  核心模块 '{core}' -> {found[0]}: {len(docs)} 个文档")

        print(f"\n核心模块覆盖率: {covered}/{len(core_modules)}")
        assert covered >= 3, "至少 3 个核心模块应跨文档覆盖"

    def test_simulated_retrieval(self, processor, docs_dir):
        """模拟检索测试：用关键词搜索验证切分质量。

        模拟 RAG 检索场景，验证切分后的 chunk 能否正确响应典型查询。
        """
        if not docs_dir.exists():
            pytest.skip(f"目录不存在: {docs_dir}")

        all_chunks = processor.process_directory(docs_dir)

        # 模拟查询
        queries = [
            ("采购入库单怎么创建", "采购"),
            ("数据库用户表有哪些字段", "jsh_user"),
            ("零售出库", "零售出库"),
            ("等价类划分", "等价类"),
            ("角色管理", "角色"),
            ("供应商信息", "供应商"),
        ]

        print("\n" + "=" * 80)
        print("模拟检索测试")
        print("=" * 80)

        for query, expected_keyword in queries:
            # 简单关键词匹配（模拟向量检索）
            matched = []
            for chunk in all_chunks:
                if expected_keyword in chunk.content:
                    matched.append(chunk)

            print(f"\n查询: '{query}'")
            print(f"  关键词: '{expected_keyword}'")
            print(f"  匹配数: {len(matched)}")
            if matched:
                print(f"  Top 3 结果:")
                for i, chunk in enumerate(matched[:3]):
                    source = chunk.metadata.source_doc
                    module = chunk.metadata.module
                    ct = chunk.metadata.chunk_type.value
                    print(f"    [{i+1}] [{ct}] {source} > {module}")
                    print(f"        {chunk.content[:80]}...")

            # 每个查询至少有一个匹配
            assert len(matched) >= 1, f"查询 '{query}' 应该至少匹配 1 个 chunk"

    def test_content_quality_check(self, processor, docs_dir):
        """内容质量检查：验证切分结果的完整性和一致性。"""
        if not docs_dir.exists():
            pytest.skip(f"目录不存在: {docs_dir}")

        all_chunks = processor.process_directory(docs_dir)

        print("\n" + "=" * 80)
        print("内容质量检查")
        print("=" * 80)

        issues = []

        # 检查 1: 空内容
        empty_chunks = [c for c in all_chunks if not c.content.strip()]
        if empty_chunks:
            issues.append(f"空内容 chunk: {len(empty_chunks)}")

        # 检查 2: 过长内容
        long_chunks = [c for c in all_chunks if len(c.content) > 8000]
        if long_chunks:
            issues.append(f"超长 chunk (>8000字符): {len(long_chunks)}")
            for chunk in long_chunks[:3]:
                print(f"  过长: {chunk.chunk_id} ({len(chunk.content)} 字符)")

        # 检查 3: 图片 chunk 必须有 image_metadata
        image_no_meta = [
            c for c in all_chunks
            if c.metadata.chunk_type == ChunkType.IMAGE and c.image_metadata is None
        ]
        if image_no_meta:
            issues.append(f"图片 chunk 缺少 image_metadata: {len(image_no_meta)}")

        # 检查 4: 重复 chunk_id
        ids = [c.chunk_id for c in all_chunks]
        dup_ids = [id for id in ids if ids.count(id) > 1]
        if dup_ids:
            issues.append(f"重复 chunk_id: {len(set(dup_ids))}")

        # 检查 5: 上下文前缀完整性
        no_prefix = [c for c in all_chunks if not c.content.startswith("《")]
        if no_prefix:
            issues.append(f"缺少上下文前缀: {len(no_prefix)}")

        # 输出结果
        content_lengths = [len(c.content) for c in all_chunks]
        print(f"内容长度统计:")
        print(f"  最短: {min(content_lengths)} 字符")
        print(f"  最长: {max(content_lengths)} 字符")
        print(f"  平均: {sum(content_lengths)//len(content_lengths)} 字符")
        print(f"  中位数: {sorted(content_lengths)[len(content_lengths)//2]} 字符")

        if issues:
            print(f"\n发现问题 ({len(issues)} 项):")
            for issue in issues:
                print(f"  [!] {issue}")
        else:
            print(f"\n[OK] 所有质量检查通过")

        # 断言
        assert len(empty_chunks) == 0, "不应有空内容 chunk"
        assert len(image_no_meta) == 0, "图片 chunk 必须有 image_metadata"
        assert len(set(dup_ids)) == 0, "不应有重复 chunk_id"
