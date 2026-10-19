"""文档处理数据结构定义。

定义文档切分过程中的核心数据模型，包括图片元数据、文本块元数据和文档块。

主键设计（Phase 2.5）：
    chunk_id = ``{doc_slug}_{type}_{pXXX}_{iXXXX}``
    - 位置型、纯 ASCII、跨次处理稳定；
    - 内容哈希不再作为主键，降级为 ``content_hash`` 普通字段；
    - 可读性由 metadata（doc_title/module/section_title/page）承担。
"""

from __future__ import annotations

import hashlib
import logging
import re
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# 文档 slug 映射：文件名 -> 2 字母 ASCII 缩写
DOC_SLUG_MAP: dict[str, str] = {
    "ERP_UI_Web_Testing_Guide.md": "tg",
    "管伊佳ERP-功能清单.md": "fl",
    "管伊佳ERP-数据库设计.md": "ds",
    "管伊佳ERP-用户手册v3.6.pdf": "um",
}

# chunk 类型 -> 主键中的 3 字母缩写
CHUNK_TYPE_ABBR: dict[str, str] = {
    "text": "txt",
    "image": "img",
    "table": "tbl",
}

# 模块名归一化：文档结构/版本分区 -> 稳定业务模块名
_MODULE_ALIASES: dict[str, str] = {
    "查询报表": "报表查询",
    "报表与分析": "报表查询",
    "库存管理": "仓库管理",
    "基础数据": "基础资料",
    "系统配置": "系统管理",
}

_FRONT_MATTER_MODULES = {"目录", "附录", "前言", "序言", "封面", "扉页"}
_VERSION_PARTITION_MODULES = {"开源版本", "全部功能版本", "专业版本", "免费版本"}

_LEADING_NUMBER_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*[\.、\s]*|[一二三四五六七八九十]+[、\.\s]+)"
)


class ChunkType(str, Enum):
    """文档块类型。"""

    TEXT = "text"
    IMAGE = "image"
    TABLE = "table"


def _slug_for(source_doc: str) -> str:
    """返回文档的稳定 ASCII slug。

    已知文档走 ``DOC_SLUG_MAP``；未知文档由文件名派生稳定 slug，
    不再退化成所有未知文档共享的 ``xx``。
    """
    name = Path(source_doc).name
    if name in DOC_SLUG_MAP:
        return DOC_SLUG_MAP[name]

    # 去掉扩展名，保留 ASCII 字母数字；中文文件名则用短哈希
    stem = Path(name).stem
    ascii_slug = re.sub(r"[^a-z0-9]+", "", stem.lower())
    if ascii_slug:
        slug = ascii_slug[:8]
    else:
        slug = f"doc{hashlib.md5(name.encode('utf-8')).hexdigest()[:5]}"
    logger.warning("未登记文档 slug，使用派生值: %s -> %s", name, slug)
    return slug


def generate_chunk_id(
    source_doc: str,
    chunk_type: ChunkType | str,
    page: int,
    chunk_index: int,
) -> str:
    """生成位置型 ASCII 主键。

    Format: ``{doc_slug}_{type_abbr}_p{page:03d}_i{index:04d}``

    Examples:
        um_img_p004_i0133
        um_txt_p009_i0145
        ds_tbl_p000_i0088

    位置型主键只依赖文档结构，不依赖 OCR 行序或 caption 文本，
    因此跨次处理稳定，可用于幂等重建、增量更新和评测集引用。

    Args:
        source_doc: 源文档文件名（如 ``foo.md`` / ``bar.pdf``）
        chunk_type: ``text`` / ``image`` / ``table``
        page: 页码（Markdown 统一为 0）
        chunk_index: 文档内全局序号（0-based）

    Returns:
        纯 ASCII 主键，长度 <= 32
    """
    type_value = chunk_type.value if isinstance(chunk_type, ChunkType) else str(chunk_type)
    type_abbr = CHUNK_TYPE_ABBR.get(type_value, "unk")
    slug = _slug_for(source_doc)
    safe_page = max(0, int(page or 0))
    safe_index = max(0, int(chunk_index or 0))
    index_part = f"{safe_index:04d}" if safe_index <= 9999 else str(safe_index)
    return f"{slug}_{type_abbr}_p{safe_page:03d}_i{index_part}"


def compute_content_hash(content: str) -> str:
    """计算内容哈希（16 位十六进制），仅用于变更检测，不参与主键。"""
    return hashlib.md5(content.encode("utf-8")).hexdigest()[:16]


def normalize_module(module: str) -> str:
    """把文档结构名归一化为稳定业务模块名。

    - 去掉前导编号（``1. Web UI 测试方法论`` -> ``Web UI 测试方法论``）；
    - 版本分区（``开源版本``/``全部功能版本``）-> ``功能清单``；
    - 目录/附录等 -> ``front_matter``；
    - 常见叫法统一（``查询报表`` -> ``报表查询``）。
    """
    if not module:
        return "未分类"

    value = module.strip().replace("[插件]", "").replace("[揷件]", "").strip()
    value = _LEADING_NUMBER_RE.sub("", value).strip()
    value = re.sub(r"\s+", " ", value)

    if not value:
        return "未分类"
    if value in _FRONT_MATTER_MODULES:
        return "front_matter"
    if value in _VERSION_PARTITION_MODULES:
        return "功能清单"
    return _MODULE_ALIASES.get(value, value)


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

    @field_validator("module", mode="after")
    @classmethod
    def _normalize_module_field(cls, value: str) -> str:
        return normalize_module(value)


class DocumentChunk(BaseModel):
    """文档块：一个可检索的最小语义单元。

    每个 DocumentChunk 包含：
    - chunk_id: 位置型全局唯一标识（ASCII）
    - content: 完整文本内容（已含上下文前缀，可独立理解）
    - metadata: 来源与分类元数据
    - image_metadata: 仅当 chunk_type=IMAGE 时填充
    - content_hash: 内容哈希，仅用于变更检测
    """

    chunk_id: str = Field(
        description="位置型主键: {doc_slug}_{type}_{pXXX}_{iXXXX}，纯 ASCII、跨次稳定"
    )
    content: str = Field(description="块的完整文本内容（含上下文前缀）")
    metadata: ChunkMetadata = Field(description="元数据")
    image_metadata: Optional[ImageMetadata] = Field(
        default=None, description="图片元数据（仅 chunk_type=IMAGE 时有值）"
    )
    content_hash: str = Field(
        default="", description="md5(content)[:16]，用于变更检测，非主键"
    )

    @model_validator(mode="after")
    def _fill_content_hash(self) -> "DocumentChunk":
        if not self.content_hash:
            self.content_hash = compute_content_hash(self.content)
        return self
