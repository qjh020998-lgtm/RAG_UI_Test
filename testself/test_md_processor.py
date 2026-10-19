"""Markdown 文档处理器测试。

测试三种 MD 文档的切分逻辑，输出直观的统计信息和示例。
"""

import json
from pathlib import Path

import pytest

from tools.document.md_filtter import MarkdownProcessor
from tools.document.schema import ChunkType


@pytest.fixture
def md_processor():
    """创建 Markdown 处理器实例。"""
    return MarkdownProcessor()


@pytest.fixture
def docs_dir():
    """文档目录路径。"""
    return Path(__file__).parent.parent / "docs"  # ✓ 正确：D:\RAG\RAG_UI_Test\docs


class TestMarkdownProcessor:
    """Markdown 处理器测试套件。"""

    def test_function_list_processing(self, md_processor, docs_dir):
        """测试功能清单文档切分。"""
        file_path = docs_dir / "管伊佳ERP-功能清单.md"
        if not file_path.exists():
            pytest.skip(f"文件不存在: {file_path}")

        chunks = md_processor.parse(file_path)

        # 基本统计
        print("\n" + "=" * 80)
        print("功能清单文档切分结果")
        print("=" * 80)
        print(f"总 chunk 数: {len(chunks)}")

        # 按模块统计
        modules = {}
        for chunk in chunks:
            module = chunk.metadata.module or "未分类"
            modules.setdefault(module, []).append(chunk)

        print(f"模块数: {len(modules)}")
        print("\n模块分布:")
        for module, module_chunks in sorted(modules.items()):
            print(f"  {module}: {len(module_chunks)} chunks")

        # 展示前 3 个 chunk
        print("\n前 3 个 chunk 示例:")
        for i, chunk in enumerate(chunks[:3]):
            print(f"\n--- Chunk {i+1} ---")
            print(f"ID: {chunk.chunk_id}")
            print(f"模块: {chunk.metadata.module}")
            print(f"小节: {chunk.metadata.section_title}")
            print(f"类型: {chunk.metadata.chunk_type.value}")
            print(f"内容预览 ({len(chunk.content)} 字符):")
            print(chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content)

        # 断言
        assert len(chunks) >= 20, "功能清单应该至少有 20 个 chunk"
        assert all(c.metadata.chunk_type == ChunkType.TEXT for c in chunks)
        assert all(c.metadata.source_doc == "管伊佳ERP-功能清单.md" for c in chunks)

    def test_database_design_processing(self, md_processor, docs_dir):
        """测试数据库设计文档切分。"""
        file_path = docs_dir / "管伊佳ERP-数据库设计.md"
        if not file_path.exists():
            pytest.skip(f"文件不存在: {file_path}")

        chunks = md_processor.parse(file_path)

        # 基本统计
        print("\n" + "=" * 80)
        print("数据库设计文档切分结果")
        print("=" * 80)
        print(f"总 chunk 数: {len(chunks)}")
        print(f"全部为表格类型: {all(c.metadata.chunk_type == ChunkType.TABLE for c in chunks)}")

        # 展示前 3 个表结构
        print("\n前 3 个表结构示例:")
        for i, chunk in enumerate(chunks[:3]):
            print(f"\n--- 表 {i+1}: {chunk.metadata.section_title} ---")
            print(f"ID: {chunk.chunk_id}")
            print(f"中文名: {chunk.metadata.module}")
            print(f"内容预览 ({len(chunk.content)} 字符):")
            # 只显示前 5 行
            lines = chunk.content.split("\n")
            print("\n".join(lines[:8]))
            if len(lines) > 8:
                print(f"... (还有 {len(lines) - 8} 行)")

        # 断言
        assert len(chunks) >= 25, "数据库设计应该至少有 25 张表"
        assert all(c.metadata.chunk_type == ChunkType.TABLE for c in chunks)
        # 检查是否包含关键表
        table_names = [c.metadata.section_title for c in chunks]
        assert any("jsh_user" in name for name in table_names), "应该包含用户表"
        assert any("jsh_depot" in name for name in table_names), "应该包含仓库表"

    def test_testing_guide_processing(self, md_processor, docs_dir):
        """测试测试指南文档切分。"""
        file_path = docs_dir / "ERP_UI_Web_Testing_Guide.md"
        if not file_path.exists():
            pytest.skip(f"文件不存在: {file_path}")

        chunks = md_processor.parse(file_path)

        # 基本统计
        print("\n" + "=" * 80)
        print("测试指南文档切分结果")
        print("=" * 80)
        print(f"总 chunk 数: {len(chunks)}")

        # 按模块统计
        modules = {}
        for chunk in chunks:
            module = chunk.metadata.module or "未分类"
            modules.setdefault(module, []).append(chunk)

        print(f"模块数: {len(modules)}")
        print("\n模块分布:")
        for module, module_chunks in sorted(modules.items()):
            print(f"  {module}: {len(module_chunks)} chunks")

        # 展示测试方法论部分的 chunk
        print("\n测试方法论部分示例:")
        methodology_chunks = [c for c in chunks if "方法论" in c.metadata.module or "Web UI" in c.metadata.module]
        for i, chunk in enumerate(methodology_chunks[:2]):
            print(f"\n--- Chunk: {chunk.metadata.section_title} ---")
            print(f"内容预览 ({len(chunk.content)} 字符):")
            print(chunk.content[:300] + "..." if len(chunk.content) > 300 else chunk.content)

        # 断言
        assert len(chunks) >= 30, "测试指南应该至少有 30 个 chunk"
        assert all(c.metadata.chunk_type == ChunkType.TEXT for c in chunks)

    def test_all_md_documents_summary(self, md_processor, docs_dir, tmp_path):
        """测试所有 MD 文档的综合统计。"""
        md_files = list(docs_dir.glob("*.md"))
        if not md_files:
            pytest.skip("没有找到 MD 文件")

        print("\n" + "=" * 80)
        print("所有 Markdown 文档切分汇总")
        print("=" * 80)

        total_chunks = 0
        file_stats = []

        for md_file in sorted(md_files):
            chunks = md_processor.parse(md_file)
            total_chunks += len(chunks)

            # 统计各类型
            type_counts = {}
            for chunk in chunks:
                t = chunk.metadata.chunk_type.value
                type_counts[t] = type_counts.get(t, 0) + 1

            file_stats.append({
                "file": md_file.name,
                "chunks": len(chunks),
                "types": type_counts,
            })

        print(f"\n总计: {len(md_files)} 个文件, {total_chunks} 个 chunks\n")
        print(f"{'文件名':<40} {'Chunk数':>8} {'类型分布'}")
        print("-" * 80)
        for stat in file_stats:
            types_str = ", ".join(f"{k}:{v}" for k, v in stat["types"].items())
            print(f"{stat['file']:<40} {stat['chunks']:>8} {types_str}")

        # 导出 JSONL 预览
        # P1-2：testself 只写 pytest tmp_path
        output_path = tmp_path / "_md_chunks_preview.jsonl"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# Markdown 文档切分预览\n")
            f.write(f"# 总计: {len(md_files)} 个文件\n")
            f.write("=" * 80 + "\n\n")
            
            for md_file in sorted(md_files):
                chunks = md_processor.parse(md_file)
                f.write(f"\n{'=' * 80}\n")
                f.write(f"文件: {md_file.name}\n")
                f.write(f"{'=' * 80}\n\n")
                
                for i, chunk in enumerate(chunks[:5]):
                    f.write(f"--- Chunk {i+1} ---\n")
                    f.write(f"ID: {chunk.chunk_id}\n")
                    f.write(f"文档: {chunk.metadata.source_doc}\n")
                    f.write(f"模块: {chunk.metadata.module or '未分类'}\n")
                    f.write(f"小节: {chunk.metadata.section_title or '未分类'}\n")
                    f.write(f"类型: {chunk.metadata.chunk_type.value}\n")
                    f.write(f"页码: {chunk.metadata.page or 'N/A'}\n")
                    f.write(f"内容长度: {len(chunk.content)} 字符\n\n")
                    f.write("内容:\n")
                    f.write("-" * 40 + "\n")
                    for line in chunk.content.split("\n"):
                        f.write(line + "\n")
                    f.write("-" * 40 + "\n\n")
        print(f"\n预览已导出: {output_path}")
        assert total_chunks >= 80, "所有 MD 文档应该有 80+ chunks"
