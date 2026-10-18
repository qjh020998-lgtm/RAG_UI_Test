"""文档处理数据结构定义。

定义文档切分过程中的核心数据模型，包括图片元数据、文本块元数据和文档块。
"""

from enum import Enum
from typing import Optional

import hashlib

from pydantic import BaseModel, Field


# 文档 slug 映射：文件名 -> 2 字母 ASCII 缩写
DOC_SLUG_MAP: dict[str, str] = {
    "ERP_UI_Web_Testing_Guide.md": "tg",
    "管伊佳ERP-功能清单.md": "fl",
    "管伊佳ERP-数据库设计.md": "ds",
    "管伊佳ERP-用户手册v3.6.pdf": "um",
}


def generate_chunk_id(source_doc: str, content: str) -> str:
    """Generate a stable ASCII chunk ID.

    Format: {doc_slug}_{md5(content)[:8]}
    Example: tg_a3f1b2c4, um_7e9d0f1a

    Args:
        source_doc: Source document filename (e.g. "foo.md", "bar.pdf")
        content: The final chunk content to hash

    Returns:
        ASCII string, max 11 chars (2 slug + 1 _ + 8 hex)
    """
    slug = DOC_SLUG_MAP.get(source_doc, "xx")
    content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{content_hash}"


class ChunkType(str, Enum):
    """文档块类型。"""

    TEXT = "text"
    IMAGE = "image"
    TABLE = "table"


class ImageMetadata(BaseModel):
    """图片元数据，记录图片在文档中的位置、内容和关联信息。

    设计原则：
    - 不冗余：每个字段都有明确用途，不为将来扩展预留无用字段
    - 可追溯：通过 image_id 和 image_path 可以唯一定位和访问图片
    - 可检索：ocr_text 和 caption 提供两种检索维度（精确匹配 + 语义理解）
    - 可关联：related_section 和 related_text 建立图片与文本的对应关系
    """

    image_id: str = Field(description="图片唯一标识，格式：{doc_name}_p{page}_{idx}")
    page: int = Field(description="图片所在页码（1-based）")
    index_in_page: int = Field(description="图片在页内的序号（0-based）")
    width: int = Field(description="图片宽度（像素）")
    height: int = Field(description="图片高度（像素）")
    image_path: str = Field(description="图片存储相对路径")
    ocr_text: str = Field(default="", description="OCR 识别的原始文本，用于精确检索按钮名/字段名")
    caption: str = Field(default="", description="自动生成的结构化图注，用于语义检索")
    related_section: str = Field(default="", description="所属功能小节标题，用于模块级过滤")
    related_text: str = Field(default="", description="与图片相邻的描述性文本，建立图文对应关系")


class ChunkMetadata(BaseModel):
    """文档块元数据，记录来源、层级和分类信息。"""

    source_doc: str = Field(description="源文档文件名")
    doc_title: str = Field(description="文档标题/简称")
    module: str = Field(default="", description="所属业务模块（如：采购管理、系统管理）")
    section_title: str = Field(default="", description="所属小节标题")
    page: int = Field(default=0, description="起始页码（PDF 适用，MD 为 0）")
    chunk_type: ChunkType = Field(description="块类型：text / image / table")
    chunk_index: int = Field(description="块在文档内的全局序号（0-based）")
    is_plugin: bool = Field(default=False, description="是否为插件功能（来自 PDF ToC 中的 [插件] 标记）")


class DocumentChunk(BaseModel):
    """文档块：一个可检索的最小语义单元。

    每个 DocumentChunk 包含：
    - chunk_id: 全局唯一标识
    - content: 完整文本内容（已含上下文前缀，可独立理解）
    - metadata: 来源与分类元数据
    - image_metadata: 仅当 chunk_type=IMAGE 时填充
    """

    chunk_id: str = Field(description="块唯一标识，格式: {doc_slug}_{md5(content)[:8]}，纯 ASCII，幂等")
    content: str = Field(description="块的完整文本内容（含上下文前缀）")
    metadata: ChunkMetadata = Field(description="元数据")
    image_metadata: Optional[ImageMetadata] = Field(
        default=None, description="图片元数据（仅 chunk_type=IMAGE 时有值）"
    )
