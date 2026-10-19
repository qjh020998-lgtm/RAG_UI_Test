"""文档处理工具包。

提供统一的文档处理入口，支持 Markdown 和 PDF 格式的文档解析与切分。
"""

from pathlib import Path
from typing import List, Optional

from tools.document.md_filtter import MarkdownProcessor
from tools.document.pdf_filtter import PDFProcessor
from tools.document.schema import DocumentChunk


class DocumentProcessor:
    """统一文档处理器，支持 MD 和 PDF 格式。"""

    def __init__(
        self,
        image_output_dir: str | Path = "knowledge-store/images",
        *,
        retry_failed_ocr: bool = True,
        ocr_cache_path: str | Path | None = None,
        ocr_failed_path: str | Path | None = None,
        max_image_side: int = 2000,
    ):
        """初始化。

        Args:
            image_output_dir: PDF 图片输出目录
            retry_failed_ocr: 是否重试上次失败的 OCR（失败结果不再写入成功缓存）
            ocr_cache_path: OCR 成功缓存路径（默认 <项目根>/cache/ocr_cache.json）
            ocr_failed_path: OCR 失败记录路径（默认 <项目根>/cache/ocr_failed.json）
            max_image_side: 送 OCR 前的最长边上限（像素）
        """
        self.md_processor = MarkdownProcessor()
        self.pdf_processor = PDFProcessor(
            image_output_dir=image_output_dir,
            retry_failed_ocr=retry_failed_ocr,
            ocr_cache_path=ocr_cache_path,
            ocr_failed_path=ocr_failed_path,
            max_image_side=max_image_side,
        )

    def process_file(self, file_path: str | Path) -> List[DocumentChunk]:
        """处理单个文档文件。

        根据文件扩展名自动选择处理器：
        - .md → MarkdownProcessor
        - .pdf → PDFProcessor

        Args:
            file_path: 文档文件路径

        Returns:
            文档块列表
        """
        path = Path(file_path)
        suffix = path.suffix.lower()

        if suffix == ".md":
            return self.md_processor.parse(path)
        elif suffix == ".pdf":
            return self.pdf_processor.parse(path)
        else:
            raise ValueError(f"不支持的文件格式: {suffix}")

    def process_directory(
        self,
        docs_dir: str | Path,
        file_pattern: str = "*",
    ) -> List[DocumentChunk]:
        """处理目录下所有支持的文档文件。

        Args:
            docs_dir: 文档目录路径
            file_pattern: 文件匹配模式（默认 *，即所有文件）

        Returns:
            所有文档块列表
        """
        dir_path = Path(docs_dir)
        if not dir_path.is_dir():
            raise NotADirectoryError(f"目录不存在: {dir_path}")

        all_chunks: List[DocumentChunk] = []

        # 按文件名排序确保处理顺序稳定
        for file_path in sorted(dir_path.glob(file_pattern)):
            if file_path.is_file() and file_path.suffix.lower() in (".md", ".pdf"):
                try:
                    chunks = self.process_file(file_path)
                    all_chunks.extend(chunks)
                except Exception as exc:
                    print(f"[WARNING] 处理 {file_path.name} 失败: {exc}")

        # 注意：这里不再做跨文档全局重编号。
        # chunk_index 是「文档内序号」，chunk_id 依赖它；若按目录处理顺序重编号，
        # 新增/删除一个文档会改变后续文档的所有主键，破坏幂等性。
        return all_chunks
