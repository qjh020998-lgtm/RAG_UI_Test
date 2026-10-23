"""Milvus collection 的 schema 定义。

三个 collection（`erp_code` / `erp_test` / `erp_knowledge`）共用同一套字段，
因为 ``agent/chunking.py`` 的三层 metadata 已经是统一的 20 个键；
统一 schema 让 indexer 不需要按层写分支，也让检索代码只维护一份字段清单。

字段分四组：

- 身份：``chunk_id`` / ``layer`` / ``kind`` / ``file`` / ``class_name`` / ``name`` / ``qualname`` / ``case_id``
- 过滤：``module`` / ``priority`` / ``automation_status`` / ``page`` / ``sheet`` /
  ``markers`` / ``fixtures`` / ``scope`` / ``is_plugin``
- 定位：``chunk_index`` / ``start_line`` / ``end_line``
- 去重：``content_hash``

另有正文 ``content`` 与两个向量字段 ``dense`` / ``sparse``。

命名注意
--------
- metadata 里的 ``class`` 是 Python 保留字，写入 Milvus 时改名为 ``class_name``；
  映射关系见 :class:`FieldSpec.source_key`。
- ``page`` 在 code 层是页面名（"采购管理"），在 knowledge 层是 PDF 页码（4）；
  Milvus 字段类型固定，统一存 VARCHAR，页码转字符串。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from pymilvus import CollectionSchema, DataType, FieldSchema

DEFAULT_DENSE_DIM = 1024

# 层 -> collection 名
COLLECTION_BY_LAYER: dict[str, str] = {
    "code": "erp_code",
    "test": "erp_test",
    "knowledge": "erp_knowledge",
}

# collection 名 -> 层
LAYER_BY_COLLECTION: dict[str, str] = {name: layer for layer, name in COLLECTION_BY_LAYER.items()}

PRIMARY_KEY = "chunk_id"
PRIMARY_MAX_LENGTH = 64
CONTENT_FIELD = "content"
CONTENT_MAX_LENGTH = 65535
DENSE_FIELD = "dense"
SPARSE_FIELD = "sparse"


@dataclass(frozen=True)
class FieldSpec:
    """一个 metadata 字段的声明。"""

    name: str
    dtype: str  # varchar | int | bool | array
    max_length: int = 0
    max_capacity: int = 0
    element_max_length: int = 0
    default: Any = None
    # metadata 里的键名；与 Milvus 字段名不同时才有值（目前只有 class -> class_name）
    source_key: str = ""

    @property
    def metadata_key(self) -> str:
        return self.source_key or self.name


# 与 agent/chunking.py 的 20 个 metadata 字段一一对应
METADATA_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("layer", "varchar", max_length=16, default=""),
    FieldSpec("kind", "varchar", max_length=32, default=""),
    FieldSpec("file", "varchar", max_length=256, default=""),
    FieldSpec("class_name", "varchar", max_length=128, default="", source_key="class"),
    FieldSpec("name", "varchar", max_length=256, default=""),
    FieldSpec("qualname", "varchar", max_length=512, default=""),
    FieldSpec("module", "varchar", max_length=64, default=""),
    FieldSpec("priority", "varchar", max_length=8, default=""),
    FieldSpec("automation_status", "varchar", max_length=16, default=""),
    FieldSpec("case_id", "varchar", max_length=32, default=""),
    FieldSpec("page", "varchar", max_length=64, default=""),
    FieldSpec("sheet", "varchar", max_length=32, default=""),
    FieldSpec("markers", "array", max_capacity=8, element_max_length=32, default=()),
    FieldSpec("fixtures", "array", max_capacity=10, element_max_length=128, default=()),
    FieldSpec("scope", "varchar", max_length=16, default=""),
    FieldSpec("is_plugin", "bool", default=False),
    FieldSpec("content_hash", "varchar", max_length=32, default=""),
    FieldSpec("chunk_index", "int", default=-1),
    FieldSpec("start_line", "int", default=0),
    FieldSpec("end_line", "int", default=0),
)

METADATA_FIELD_NAMES: tuple[str, ...] = tuple(spec.name for spec in METADATA_FIELDS)
METADATA_BY_NAME: dict[str, FieldSpec] = {spec.name: spec for spec in METADATA_FIELDS}

# 需要建标量索引的过滤字段
SCALAR_INDEX_FIELDS: tuple[str, ...] = ("layer", "module", "sheet", "automation_status")

# 向量索引参数
DENSE_INDEX = {
    "index_type": "IVF_FLAT",
    "metric_type": "COSINE",
    "params": {"nlist": 128},
}
SPARSE_INDEX = {
    "index_type": "SPARSE_INVERTED_INDEX",
    "metric_type": "IP",
    "params": {},
}
SCALAR_INDEX = {"index_type": "INVERTED"}


def _to_field_schema(spec: FieldSpec) -> FieldSchema:
    if spec.dtype == "varchar":
        return FieldSchema(spec.name, DataType.VARCHAR, max_length=spec.max_length)
    if spec.dtype == "int":
        return FieldSchema(spec.name, DataType.INT32)
    if spec.dtype == "bool":
        return FieldSchema(spec.name, DataType.BOOL)
    if spec.dtype == "array":
        return FieldSchema(
            spec.name,
            DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_capacity=spec.max_capacity,
            max_length=spec.element_max_length,
        )
    raise ValueError(f"未知字段类型: {spec.dtype}（字段 {spec.name}）")


def build_schema(collection_name: str, dense_dim: int = DEFAULT_DENSE_DIM) -> CollectionSchema:
    """构建一个 collection 的 schema。

    Args:
        collection_name: ``erp_code`` / ``erp_test`` / ``erp_knowledge``
        dense_dim: BGE-M3 dense 维度，默认 1024
    """
    fields: list[FieldSchema] = [
        FieldSchema(PRIMARY_KEY, DataType.VARCHAR, is_primary=True, max_length=PRIMARY_MAX_LENGTH),
        *[_to_field_schema(spec) for spec in METADATA_FIELDS],
        FieldSchema(CONTENT_FIELD, DataType.VARCHAR, max_length=CONTENT_MAX_LENGTH),
        FieldSchema(DENSE_FIELD, DataType.FLOAT_VECTOR, dim=dense_dim),
        FieldSchema(SPARSE_FIELD, DataType.SPARSE_FLOAT_VECTOR),
    ]
    return CollectionSchema(
        fields=fields,
        description=f"ERP test context — {collection_name}（layer={LAYER_BY_COLLECTION.get(collection_name, '?')}）",
    )


def iter_index_specs() -> Iterable[tuple[str, dict]]:
    """按 ``(字段, 索引参数)`` 产出所有要建的索引。"""
    yield DENSE_FIELD, DENSE_INDEX
    yield SPARSE_FIELD, SPARSE_INDEX
    for name in SCALAR_INDEX_FIELDS:
        yield name, SCALAR_INDEX


def build_row(
    chunk_id: str,
    metadata: dict,
    content: str,
    dense: list[float],
    sparse: dict[int, float],
) -> dict:
    """把 ``chunk_id + metadata + content + 向量`` 转成 Milvus 可插入的一行。

    - ``class`` 映射为 ``class_name``；
    - ``page`` 统一转字符串（code 是页面名，knowledge 是页码）；
    - 空值补默认值，避免 Milvus 插入时字段缺失；
    - ARRAY 字段做长度截断，VARCHAR 字段做 ``max_length`` 截断。
    """
    row: dict[str, Any] = {
        PRIMARY_KEY: str(chunk_id),
        CONTENT_FIELD: (content or "")[:CONTENT_MAX_LENGTH],
        DENSE_FIELD: list(dense),
        SPARSE_FIELD: sparse,
    }

    for spec in METADATA_FIELDS:
        value = metadata.get(spec.metadata_key, spec.default)

        if spec.dtype == "varchar":
            text = "" if value is None else str(value)
            row[spec.name] = text[: spec.max_length]
        elif spec.dtype == "int":
            try:
                row[spec.name] = int(value)
            except (TypeError, ValueError):
                row[spec.name] = int(spec.default)
        elif spec.dtype == "bool":
            row[spec.name] = bool(value)
        elif spec.dtype == "array":
            values = list(value or ())
            row[spec.name] = [
                str(item)[: spec.element_max_length] for item in values[: spec.max_capacity]
            ]

    return row
