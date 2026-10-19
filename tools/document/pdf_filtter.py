"""PDF 文档处理器。

使用 PyMuPDF 提取 PDF 文本和图片，结合 RapidOCR 处理截图，
使用 LangChain 的 RecursiveCharacterTextSplitter 进行文本切分。
确保图片与对应功能模块的描述信息关联，生成带完整元数据的文档块。
"""

import hashlib
import io
import json
import logging
import re
import time
from pathlib import Path
from typing import List, Optional

import pymupdf as fitz  # PyMuPDF
from langchain_text_splitters import RecursiveCharacterTextSplitter
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

from tools.document.schema import (
    ChunkMetadata,
    ChunkType,
    DocumentChunk,
    ImageMetadata,
    compute_content_hash,
    generate_chunk_id,
)

logger = logging.getLogger(__name__)

# 项目根目录：tools/document/pdf_filtter.py -> parents[2]
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OCR_CACHE = _PROJECT_ROOT / "cache" / "ocr_cache.json"
_DEFAULT_OCR_FAILED = _PROJECT_ROOT / "cache" / "ocr_failed.json"

# OCR 文本行排序的 Y 轴容差（像素）：同一行内的文字按 X 排序
_OCR_ROW_TOLERANCE = 8


class Section:
    """PDF 小节信息（来自 ToC 书签）。"""

    __slots__ = ("module", "title", "start_page", "end_page", "is_plugin", "level")

    def __init__(
        self,
        module: str,
        title: str,
        start_page: int,
        end_page: int = 0,
        is_plugin: bool = False,
        level: int = 2,
    ):
        self.module = module
        self.title = title
        self.start_page = start_page
        self.end_page = end_page
        self.is_plugin = is_plugin
        self.level = level

    def __repr__(self) -> str:
        return f"Section({self.module} > {self.title}, p.{self.start_page}-{self.end_page})"


# 页眉页脚正则（过滤提取时的噪声）
_HEADER_RE = re.compile(r"管伊佳ERP")
_URL_RE = re.compile(r"^https?://gitee\.com")
_PAGE_NUM_RE = re.compile(r"^\s*\d+\s*$")


class PDFProcessor:
    """PDF 文档处理器。"""

    PREFIX_TEMPLATE = "《{doc_title}》 > {module} > {section_title}（p.{page}）"

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
            image_output_dir: 图片输出目录
            retry_failed_ocr: 是否重试上次失败的 OCR
            ocr_cache_path: OCR 成功缓存路径（默认项目根 cache/ocr_cache.json）
            ocr_failed_path: OCR 失败记录路径（默认项目根 cache/ocr_failed.json）
            max_image_side: 送 OCR 前的最长边上限（像素）
        """
        self.image_output_dir = Path(image_output_dir)
        self.ocr = RapidOCR()
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=600,
            chunk_overlap=60,
            separators=["\n\n", "\n", "。", "；", "，", " "],
        )

        # OCR cache (JSON-based)
        self._cache_path = Path(ocr_cache_path) if ocr_cache_path else _DEFAULT_OCR_CACHE
        self._failed_path = (
            Path(ocr_failed_path) if ocr_failed_path else _DEFAULT_OCR_FAILED
        )
        self._retry_failed = retry_failed_ocr
        self._max_image_side = max_image_side
        self._ocr_cache = self._load_json_dict(self._cache_path)
        self._ocr_failed = self._load_json_dict(self._failed_path)
        self._cache_dirty = False
        self._failed_dirty = False

    def parse(
        self,
        file_path: str | Path,
        image_output_dir: Optional[str | Path] = None,
    ) -> List[DocumentChunk]:
        """解析 PDF 文件，返回所有文档块（文本 + 图片）。"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        doc_name = path.stem
        doc_title = "管伊佳ERP用户手册"
        out_dir = Path(image_output_dir) if image_output_dir else self.image_output_dir

        doc = fitz.open(str(path))
        try:
            # Step 1: 解析 ToC
            sections = self._parse_toc(doc)

            # Step 2: 提取图片并保存 + OCR
            image_chunks = self._process_images(doc, sections, doc_name, doc_title, out_dir)

            # Step 3: 按小节提取文本并切分
            text_chunks = self._process_text(doc, sections, doc_name, doc_title)

            # Step 4: 合并、编号并生成位置型主键
            all_chunks = text_chunks + image_chunks
            for idx, chunk in enumerate(all_chunks):
                chunk.metadata.chunk_index = idx
                chunk.chunk_id = generate_chunk_id(
                    chunk.metadata.source_doc,
                    chunk.metadata.chunk_type,
                    chunk.metadata.page,
                    idx,
                )
                chunk.content_hash = compute_content_hash(chunk.content)

            return all_chunks
        finally:
            self._save_ocr_cache()
            self._save_ocr_failed()
            doc.close()

    # ==================== ToC 解析 ====================

    def _parse_toc(self, doc: fitz.Document) -> List[Section]:
        """解析 PDF 书签目录，构建小节列表。"""
        toc = doc.get_toc()
        sections: List[Section] = []
        current_module = ""

        for level, title, page in toc:
            # 清理标题
            clean_title = self._clean_toc_title(title)
            is_plugin = "[插件]" in title or "[揷件]" in title
            clean_title = clean_title.replace("[插件]", "").replace("[揷件]", "").strip()

            if level == 1:
                current_module = clean_title
            elif level == 2:
                sections.append(
                    Section(
                        module=current_module,
                        title=clean_title,
                        start_page=page,
                        is_plugin=is_plugin,
                        level=level,
                    )
                )

        # 计算 end_page
        for i, sec in enumerate(sections):
            if i + 1 < len(sections):
                sec.end_page = sections[i + 1].start_page - 1
            else:
                sec.end_page = doc.page_count

        return sections

    def _clean_toc_title(self, title: str) -> str:
        """清理 ToC 标题（去除编号、多余空格）。"""
        # 去除前导编号（如 "1."、"四、"）
        title = re.sub(r"^\d+[\.\s]*", "", title)
        title = re.sub(r"^[一二三四五六七八九十]+[、\.\s]+", "", title)
        return title.strip()

    # ==================== 文本处理 ====================

    def _process_text(
        self,
        doc: fitz.Document,
        sections: List[Section],
        doc_name: str,
        doc_title: str,
    ) -> List[DocumentChunk]:
        """按小节提取文本并切分为文档块。"""
        chunks: List[DocumentChunk] = []
        chunk_index = 0

        for sec in sections:
            # skip plugin sections
            if sec.is_plugin:
                continue

            # 提取该小节跨页的全部文本
            section_text = self._extract_section_text(doc, sec)
            if not section_text.strip():
                continue

            # 短小节直接作为一个 chunk
            if len(section_text) <= 600:
                chunk = self._build_text_chunk(
                    doc_name, doc_title, sec, section_text, chunk_index
                )
                chunks.append(chunk)
                chunk_index += 1
            else:
                # 长小节二次切分
                sub_texts = self.text_splitter.split_text(section_text)
                for sub_text in sub_texts:
                    chunk = self._build_text_chunk(
                        doc_name, doc_title, sec, sub_text, chunk_index
                    )
                    chunks.append(chunk)
                    chunk_index += 1

        return chunks

    def _extract_section_text(self, doc: fitz.Document, sec: Section) -> str:
        """提取一个小节跨页范围内的全部文本，过滤页眉页脚。"""
        text_parts: List[str] = []

        for page_no in range(sec.start_page, sec.end_page + 1):
            if page_no < 1 or page_no > doc.page_count:
                continue

            page = doc[page_no - 1]  # PyMuPDF 0-based
            blocks = page.get_text("dict")["blocks"]

            for block in blocks:
                if block.get("type") != 0:  # 只处理文本块
                    continue
                for line in block.get("lines", []):
                    line_text = ""
                    for span in line.get("spans", []):
                        line_text += span.get("text", "")
                    line_text = line_text.strip()
                    # 过滤页眉页脚
                    if not line_text:
                        continue
                    if _HEADER_RE.search(line_text) or _URL_RE.search(line_text):
                        continue
                    if _PAGE_NUM_RE.match(line_text):
                        continue
                    text_parts.append(line_text)

        return "\n".join(text_parts)

    def _build_text_chunk(
        self,
        doc_name: str,
        doc_title: str,
        sec: Section,
        text: str,
        index: int,
    ) -> DocumentChunk:
        """构建一个文本文档块。"""
        prefix = self.PREFIX_TEMPLATE.format(
            doc_title=doc_title,
            module=sec.module,
            section_title=sec.title,
            page=sec.start_page,
        )
        content = f"{prefix}\n{text}"

        # 主键在 parse() 合并 text + image 后统一按文档内序号生成
        chunk_id = ""

        metadata = ChunkMetadata(
            source_doc=f"{doc_name}.pdf",
            doc_title=doc_title,
            module=sec.module,
            section_title=sec.title,
            page=sec.start_page,
            chunk_type=ChunkType.TEXT,
            chunk_index=index,
            is_plugin=sec.is_plugin,
        )

        return DocumentChunk(
            chunk_id=chunk_id,
            content=content,
            metadata=metadata,
        )

    # ==================== 图片处理 ====================

    def _process_images(
        self,
        doc: fitz.Document,
        sections: List[Section],
        doc_name: str,
        doc_title: str,
        output_dir: Path,
    ) -> List[DocumentChunk]:
        """提取所有图片 → OCR → 生成图注 → 构建图片文档块。"""
        chunks: List[DocumentChunk] = []
        output_dir.mkdir(parents=True, exist_ok=True)

        # 预计算插件页码集合，避免逐页判断遗漏
        plugin_pages: set = set()
        for sec in sections:
            if sec.is_plugin:
                for p in range(sec.start_page, sec.end_page + 1):
                    plugin_pages.add(p)

        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            page_no = page_idx + 1
            images = page.get_images(full=True)

            # 找到当前页所属小节
            section = self._find_section_for_page(sections, page_no)

            # 跳过插件页（预计算集合，更可靠）
            if page_no in plugin_pages:
                continue

            for img_idx, img_info in enumerate(images):
                xref = img_info[0]
                try:
                    base_image = doc.extract_image(xref)
                except Exception:
                    continue

                if not base_image or "image" not in base_image:
                    continue

                # 保存图片
                ext = base_image.get("ext", "jpg")
                img_filename = f"p{page_no:03d}_{img_idx}.{ext}"
                img_path = output_dir / img_filename
                img_path.write_bytes(base_image["image"])

                # OCR
                ocr_text = self._run_ocr(str(img_path), image_bytes=base_image["image"])

                # 获取相邻文本（该页的文字内容）
                related_text = self._get_page_text(doc, page_idx)

                # 生成图注
                caption = self._generate_caption(
                    ocr_text, section, related_text
                )

                # 构建 ImageMetadata：优先存仓库相对路径（正斜杠），
                # 避免绝对路径/临时目录路径进入知识库。
                rel_path = self._repo_relative_path(img_path)
                image_meta = ImageMetadata(
                    image_id=f"{doc_name}_p{page_no}_{img_idx}",
                    page=page_no,
                    index_in_page=img_idx,
                    width=base_image.get("width", 0),
                    height=base_image.get("height", 0),
                    image_path=rel_path,
                    ocr_text=ocr_text,
                    caption=caption,
                    related_section=section.title if section else "",
                    related_text=related_text,
                )

                # 构建 DocumentChunk
                chunk = self._build_image_chunk(
                    doc_name, doc_title, section, image_meta
                )
                chunks.append(chunk)

        return chunks

    def _run_ocr(self, image_path: str, image_bytes: bytes | None = None) -> str:
        """对图片执行 OCR，成功结果写缓存，失败结果只写失败记录。

        设计要点（P0-2）：
        - 失败结果**绝不**写入成功缓存，避免一次 OOM 变成永久性故障；
        - 失败记录单独写 ``ocr_failed.json``，默认下次运行自动重试；
        - 大图先降采样再送 OCR，降低 ONNX Runtime 内存峰值。
        """
        if image_bytes is None:
            image_bytes = Path(image_path).read_bytes()
        cache_key = hashlib.md5(image_bytes).hexdigest()

        # 命中成功缓存直接返回
        if cache_key in self._ocr_cache:
            return self._ocr_cache[cache_key]

        # 上次失败且明确关闭重试时，不返回任何伪文本（更不返回 traceback）
        if cache_key in self._ocr_failed and not self._retry_failed:
            return ""

        try:
            ocr_input = self._prepare_ocr_input(image_path, image_bytes)
            result, _ = self.ocr(ocr_input)
            if not result:
                ocr_text = ""
            else:
                # result 格式: [[box, text, score], ...]
                # Y 轴按容差分行，行内按 X 排序，减少坐标抖动导致的顺序翻转
                sorted_items = sorted(
                    result,
                    key=lambda item: (
                        round(min(p[1] for p in item[0]) / _OCR_ROW_TOLERANCE),
                        min(p[0] for p in item[0]),
                    ),
                )
                lines = [str(item[1]).strip() for item in sorted_items]
                ocr_text = self._normalize_ocr_lines(lines)
        except Exception as exc:
            # 关键：不把异常文本放进 content / embedding，也不写成功缓存
            error = f"{type(exc).__name__}: {exc}"[:500]
            self._ocr_failed[cache_key] = {
                "error": error,
                "image": str(image_path),
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            self._failed_dirty = True
            logger.warning("OCR failed for %s: %s", image_path, error)
            return ""

        self._ocr_cache[cache_key] = ocr_text
        self._ocr_failed.pop(cache_key, None)
        self._cache_dirty = True
        self._failed_dirty = True
        return ocr_text

    def _prepare_ocr_input(self, image_path: str, image_bytes: bytes) -> bytes:
        """大图降采样后返回 JPEG bytes；小图直接返回原始 bytes。"""
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                if max(img.size) <= self._max_image_side:
                    return image_bytes
                img = img.convert("RGB")
                img.thumbnail(
                    (self._max_image_side, self._max_image_side),
                    Image.Resampling.LANCZOS,
                )
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=90)
                logger.info(
                    "Downsampled OCR input %s -> %sx%s",
                    Path(image_path).name,
                    img.width,
                    img.height,
                )
                return buffer.getvalue()
        except Exception:
            # 图片无法用 Pillow 打开时交给 RapidOCR 处理，由其抛错并记录失败
            return image_bytes

    @staticmethod
    def _normalize_ocr_lines(lines: list[str]) -> str:
        """清洗 OCR 行：去空行、压缩空白、去掉连续重复行。"""
        cleaned: list[str] = []
        for line in lines:
            line = re.sub(r"\s+", " ", line).strip()
            if not line:
                continue
            if cleaned and cleaned[-1] == line:
                continue
            cleaned.append(line)
        return "\n".join(cleaned)

    @staticmethod
    def _load_json_dict(path: Path) -> dict:
        """加载 JSON 字典，损坏时返回空字典。"""
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_ocr_cache(self) -> None:
        """保存 OCR 成功缓存（仅在有变更时写入）。"""
        if self._cache_dirty:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(self._ocr_cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._cache_dirty = False

    def _save_ocr_failed(self) -> None:
        """保存 OCR 失败记录（独立文件，不参与成功缓存命中）。"""
        if self._failed_dirty:
            self._failed_path.parent.mkdir(parents=True, exist_ok=True)
            self._failed_path.write_text(
                json.dumps(self._ocr_failed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._failed_dirty = False

    @staticmethod
    def _repo_relative_path(path: Path) -> str:
        """返回仓库相对路径（正斜杠）；不在仓库内时退回文件名。"""
        try:
            return path.resolve().relative_to(_PROJECT_ROOT).as_posix()
        except ValueError:
            return path.name

    def _get_page_text(self, doc: fitz.Document, page_idx: int) -> str:
        """获取指定页的文本内容（用于关联图片的上下文）。"""
        page = doc[page_idx]
        text_parts: List[str] = []
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_text = ""
                for span in line.get("spans", []):
                    line_text += span.get("text", "")
                line_text = line_text.strip()
                if line_text and not _HEADER_RE.search(line_text) and not _URL_RE.search(line_text) and not _PAGE_NUM_RE.match(line_text):
                    text_parts.append(line_text)
        return "\n".join(text_parts[:10])  # 只取前 10 行作为上下文

    def _generate_caption(
        self,
        ocr_text: str,
        section: Optional[Section],
        related_text: str,
    ) -> str:
        """根据 OCR 结果和上下文自动生成结构化图注。

        提取策略：
        - 按钮名：通常是 2-4 个汉字的短语，如 "新增"、"保存"、"审核"
        - 字段标签：通常是 "xxx:" 或 "xxx：" 格式
        - 表格表头：通常出现在表格区域
        """
        if not ocr_text:
            section_name = section.title if section else "未知"
            return f"{section_name}页面截图"

        section_name = section.title if section else ""

        # 提取可能的按钮名（2-4 个汉字的短语）
        buttons = self._extract_buttons(ocr_text)
        # 提取字段标签（xxx: 格式），过滤纯数字/时间戳噪声
        fields = self._extract_fields(ocr_text)

        caption = f"{section_name}页面截图" if section_name else "页面截图"
        if fields:
            caption += f"，包含字段：{', '.join(fields[:8])}"
        if buttons:
            caption += f"，操作按钮：{', '.join(buttons[:8])}"

        return caption

    def _extract_buttons(self, ocr_text: str) -> List[str]:
        """从 OCR 文本中提取按钮名，按固定关键词顺序输出。

        固定顺序保证同一张图两次 OCR 的 caption 顺序一致；同时优先保留
        更长的关键词（``反审核`` 不再额外产出 ``审核``）。
        """
        button_keywords = [
            "新增", "保存", "审核", "反审核", "删除", "编辑", "修改",
            "查询", "搜索", "导出", "导入", "打印", "复制", "粘贴",
            "取消", "确认", "提交", "关联", "转单", "退回", "下一步",
            "上一步", "返回", "批量", "全选", "刷新", "设置", "配置",
            "启用", "禁用",
        ]
        matched = [kw for kw in button_keywords if kw in ocr_text]
        # 去掉被更长关键词包含的短词（审核 vs 反审核）
        found = [
            kw for kw in matched
            if not any(kw != other and kw in other for other in matched)
        ]
        # 按固定关键词顺序输出
        order = {kw: i for i, kw in enumerate(button_keywords)}
        return sorted(found, key=lambda kw: order[kw])

    def _extract_fields(self, ocr_text: str) -> List[str]:
        """从 OCR 文本中提取字段标签名。

        - 必须以汉字开头，避免 ``00``、``2023-09-06`` 等时间/数字噪声；
        - 至少包含 2 个字符；
        - 去重后按字典序输出，保证 caption 稳定。
        """
        pattern = re.compile(r"([\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9_]{1,7})\s*[:：]")
        stopwords = {"http", "https", "www", "com", "cn"}
        fields: set[str] = set()
        for line in ocr_text.split("\n"):
            for match in pattern.findall(line):
                value = match.strip()
                if len(value) < 2:
                    continue
                if value.lower() in stopwords:
                    continue
                if not re.search(r"[\u4e00-\u9fff]", value):
                    continue
                fields.add(value)
        return sorted(fields)

    def _find_section_for_page(
        self, sections: List[Section], page_no: int
    ) -> Optional[Section]:
        """根据页码找到对应的小节。"""
        for sec in sections:
            if sec.start_page <= page_no <= sec.end_page:
                return sec
        return None

    def _build_image_chunk(
        self,
        doc_name: str,
        doc_title: str,
        section: Optional[Section],
        image_meta: ImageMetadata,
    ) -> DocumentChunk:
        """构建一个图片文档块。"""
        module = section.module if section else ""
        section_title = section.title if section else ""
        is_plugin = section.is_plugin if section else False

        prefix = self.PREFIX_TEMPLATE.format(
            doc_title=doc_title,
            module=module or "未分类",
            section_title=section_title or "未分类",
            page=image_meta.page,
        )

        content_parts = [prefix, f"[截图] {image_meta.caption}"]
        if image_meta.ocr_text:
            content_parts.append(f"OCR 内容：\n{image_meta.ocr_text}")

        content = "\n".join(content_parts)

        # 主键在 parse() 合并 text + image 后统一按文档内序号生成
        chunk_id = ""

        metadata = ChunkMetadata(
            source_doc=f"{doc_name}.pdf",
            doc_title=doc_title,
            module=module,
            section_title=section_title,
            page=image_meta.page,
            chunk_type=ChunkType.IMAGE,
            chunk_index=0,  # 后续统一编号
            is_plugin=is_plugin,
        )

        return DocumentChunk(
            chunk_id=chunk_id,
            content=content,
            metadata=metadata,
            image_metadata=image_meta,
        )
