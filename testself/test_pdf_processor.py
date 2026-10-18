"""PDF 处理器测试。

测试 PDF 文档的解析、图片提取、OCR 和切分逻辑，输出直观的统计信息和示例。
"""

import json
from pathlib import Path

import pytest

from tools.document.pdf_filtter import PDFProcessor
from tools.document.schema import ChunkType


@pytest.fixture
def pdf_processor(tmp_path):
    """创建 PDF 处理器实例，使用临时目录存储图片。"""
    return PDFProcessor(image_output_dir=tmp_path / "images")


@pytest.fixture
def pdf_file():
    """PDF 文件路径。"""
    return Path(__file__).parent.parent / "docs" / "管伊佳ERP-用户手册v3.6.pdf"


class TestPDFProcessor:
    """PDF 处理器测试套件。"""

    def test_toc_parsing(self, pdf_processor, pdf_file):
        """测试 ToC（书签）解析。"""
        if not pdf_file.exists():
            pytest.skip(f"文件不存在: {pdf_file}")

        import pymupdf as fitz
        doc = fitz.open(str(pdf_file))
        sections = pdf_processor._parse_toc(doc)
        doc.close()

        print("\n" + "=" * 80)
        print("PDF ToC 解析结果")
        print("=" * 80)
        print(f"小节总数: {len(sections)}")

        # 按模块分组统计
        modules = {}
        for sec in sections:
            modules.setdefault(sec.module, []).append(sec)

        print(f"模块数: {len(modules)}")
        print("\n模块分布:")
        for module, secs in sorted(modules.items(), key=lambda x: x[1][0].start_page):
            plugin_count = sum(1 for s in secs if s.is_plugin)
            plugin_str = f" (含 {plugin_count} 个插件)" if plugin_count else ""
            print(f"  {module}: {len(secs)} 小节{plugin_str} (p.{secs[0].start_page}-{secs[-1].end_page})")

        # 展示前 5 个小节
        print("\n前 5 个小节:")
        for sec in sections[:5]:
            print(f"  {sec}")

        # 断言
        assert len(sections) > 40, "过滤插件后应该有 40+ 个小节"
        assert len(modules) >= 8, "过滤插件后应该有 8+ 个模块"

    def test_image_extraction(self, pdf_processor, pdf_file, tmp_path):
        """测试图片提取和 OCR。"""
        if not pdf_file.exists():
            pytest.skip(f"文件不存在: {pdf_file}")

        # 只处理前 5 页以加速测试
        import pymupdf as fitz
        doc = fitz.open(str(pdf_file))
        sections = pdf_processor._parse_toc(doc)

        # 提取前 5 页的图片
        img_output = tmp_path / "images"
        img_output.mkdir(exist_ok=True)

        image_count = 0
        ocr_samples = []

        for page_idx in range(min(5, doc.page_count)):
            page = doc[page_idx]
            images = page.get_images(full=True)
            page_no = page_idx + 1

            for img_idx, img_info in enumerate(images):
                xref = img_info[0]
                try:
                    base = doc.extract_image(xref)
                    if base and "image" in base:
                        ext = base.get("ext", "jpg")
                        img_path = img_output / f"p{page_no:03d}_{img_idx}.{ext}"
                        img_path.write_bytes(base["image"])
                        image_count += 1

                        # 对前 3 张图片做 OCR
                        if len(ocr_samples) < 3:
                            ocr_text = pdf_processor._run_ocr(str(img_path))
                            ocr_samples.append({
                                "page": page_no,
                                "size": f"{base['width']}x{base['height']}",
                                "ocr_lines": len(ocr_text.split("\n")),
                                "ocr_preview": ocr_text[:150],
                            })
                except Exception:
                    pass

        doc.close()

        print("\n" + "=" * 80)
        print("图片提取与 OCR 测试（前 5 页）")
        print("=" * 80)
        print(f"提取图片数: {image_count}")
        print(f"OCR 样本数: {len(ocr_samples)}")

        for i, sample in enumerate(ocr_samples):
            print(f"\n--- OCR 样本 {i+1} (Page {sample['page']}) ---")
            print(f"尺寸: {sample['size']}")
            print(f"识别行数: {sample['ocr_lines']}")
            print(f"内容预览: {sample['ocr_preview']}")

        assert image_count > 5, "前 5 页应该有 5+ 张图片"
        assert len(ocr_samples) >= 2, "应该至少有 2 个 OCR 样本"

    def test_full_pdf_processing(self, pdf_processor, pdf_file):
        """测试完整 PDF 处理流程（全量）。"""
        if not pdf_file.exists():
            pytest.skip(f"文件不存在: {pdf_file}")

        chunks = pdf_processor.parse(pdf_file)

        # 统计
        text_chunks = [c for c in chunks if c.metadata.chunk_type == ChunkType.TEXT]
        image_chunks = [c for c in chunks if c.metadata.chunk_type == ChunkType.IMAGE]

        print("\n" + "=" * 80)
        print("PDF 完整处理结果")
        print("=" * 80)
        print(f"总 chunk 数: {len(chunks)}")
        print(f"  文本 chunk: {len(text_chunks)}")
        print(f"  图片 chunk: {len(image_chunks)}")

        # 按模块统计
        modules = {}
        for chunk in chunks:
            module = chunk.metadata.module or "未分类"
            modules.setdefault(module, []).append(chunk)

        print(f"\n模块分布 ({len(modules)} 个模块):")
        for module, mod_chunks in sorted(modules.items()):
            tc = sum(1 for c in mod_chunks if c.metadata.chunk_type == ChunkType.TEXT)
            ic = sum(1 for c in mod_chunks if c.metadata.chunk_type == ChunkType.IMAGE)
            print(f"  {module}: {len(mod_chunks)} (文本:{tc}, 图片:{ic})")

        # 展示图文对应示例
        print("\n图文对应示例（采购管理模块）:")
        purchase_chunks = [c for c in chunks if "采购" in c.metadata.module]
        for chunk in purchase_chunks[:5]:
            ct = chunk.metadata.chunk_type.value
            if ct == "image":
                img = chunk.image_metadata
                print(f"\n  [IMAGE] p.{chunk.metadata.page} - {img.caption if img else '无图注'}")
                if img and img.ocr_text:
                    print(f"    OCR: {img.ocr_text[:80]}...")
            else:
                print(f"\n  [TEXT] p.{chunk.metadata.page} - {chunk.metadata.section_title}")
                print(f"    {chunk.content[:100]}...")

        # 插件功能统计
        plugin_chunks = [c for c in chunks if c.metadata.is_plugin]
        print(f"\n插件功能 chunks: {len(plugin_chunks)}")

        # 导出前 20 个 chunk 到 JSONL
        # 导出前 20 个 chunk 到格式化预览
        output_path = Path(__file__).parent / "_pdf_chunks_preview.jsonl"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# PDF 文档切分预览\n")
            f.write(f"# 总计: {len(chunks)} 个 chunks\n")
            f.write("=" * 80 + "\n\n")
            
            for i, chunk in enumerate(chunks[:20]):
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

        # 断言
        assert len(chunks) > 100, "过滤插件后 PDF 应该有 100+ chunks"
        assert len(text_chunks) > 35, "过滤插件后应该至少有 35 个文本 chunk"
        assert len(image_chunks) > 60, "过滤插件后应该至少有 60 个图片 chunk"

    def test_image_text_correspondence(self, pdf_processor, pdf_file):
        """测试图片与文本的对应关系。"""
        if not pdf_file.exists():
            pytest.skip(f"文件不存在: {pdf_file}")

        chunks = pdf_processor.parse(pdf_file)
        image_chunks = [c for c in chunks if c.metadata.chunk_type == ChunkType.IMAGE]

        print("\n" + "=" * 80)
        print("图片-文本对应关系检查")
        print("=" * 80)

        # 检查有多少图片有关联的 section
        with_section = sum(1 for c in image_chunks if c.metadata.section_title)
        with_ocr = sum(1 for c in image_chunks if c.image_metadata and c.image_metadata.ocr_text)
        with_caption = sum(1 for c in image_chunks if c.image_metadata and c.image_metadata.caption)
        with_related = sum(1 for c in image_chunks if c.image_metadata and c.image_metadata.related_text)

        print(f"图片总数: {len(image_chunks)}")
        print(f"  有关联小节: {with_section} ({with_section*100//max(len(image_chunks),1)}%)")
        print(f"  有 OCR 文本: {with_ocr} ({with_ocr*100//max(len(image_chunks),1)}%)")
        print(f"  有图注: {with_caption} ({with_caption*100//max(len(image_chunks),1)}%)")
        print(f"  有关联文本: {with_related} ({with_related*100//max(len(image_chunks),1)}%)")

        # 展示几个图注示例
        print("\n图注示例:")
        captioned = [c for c in image_chunks if c.image_metadata and c.image_metadata.caption]
        for chunk in captioned[:5]:
            img = chunk.image_metadata
            print(f"\n  页面 {img.page} | 小节: {chunk.metadata.section_title}")
            print(f"  图注: {img.caption}")

        # 断言
        assert with_section > len(image_chunks) * 0.8, "80%+ 的图片应该有关联小节"
        assert with_caption > len(image_chunks) * 0.8, "80%+ 的图片应该有图注"
