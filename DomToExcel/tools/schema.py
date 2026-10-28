"""JSON Schema 片段类型。

所有子类都继承 :class:`DomToExcel.tools.base.Schema`，只负责生成 JSON Schema；
具体校验仍由 ``Schema.validate_json_schema`` 统一完成。

说明：Python 不能继承 ``bool``，所以布尔类型单独用 ``BooleanSchema``。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from DomToExcel.tools.base import Schema


class StringSchema(Schema):
    """字符串参数：描述、长度范围、枚举、可空。"""

    def __init__(
        self,
        description: str = "",
        *,
        min_length: int | None = None,
        max_length: int | None = None,
        enum: tuple[Any, ...] | list[Any] | None = None,
        nullable: bool = False,
    ) -> None:
        self._description = description
        self._min_length = min_length
        self._max_length = max_length
        self._enum = tuple(enum) if enum is not None else None
        self._nullable = nullable

    def to_json_schema(self) -> dict[str, Any]:
        schema_type: Any = ["string", "null"] if self._nullable else "string"
        schema: dict[str, Any] = {"type": schema_type}
        if self._description:
            schema["description"] = self._description
        if self._min_length is not None:
            schema["minLength"] = self._min_length
        if self._max_length is not None:
            schema["maxLength"] = self._max_length
        if self._enum is not None:
            schema["enum"] = list(self._enum)
        return schema


class IntegerSchema(Schema):
    """整数参数：占位值、描述、上下界、枚举、可空。"""

    def __init__(
        self,
        value: int = 0,
        *,
        description: str = "",
        minimum: int | None = None,
        maximum: int | None = None,
        enum: tuple[int, ...] | list[int] | None = None,
        nullable: bool = False,
    ) -> None:
        self._value = value
        self._description = description
        self._minimum = minimum
        self._maximum = maximum
        self._enum = tuple(enum) if enum is not None else None
        self._nullable = nullable

    def to_json_schema(self) -> dict[str, Any]:
        schema_type: Any = ["integer", "null"] if self._nullable else "integer"
        schema: dict[str, Any] = {"type": schema_type}
        if self._description:
            schema["description"] = self._description
        if self._minimum is not None:
            schema["minimum"] = self._minimum
        if self._maximum is not None:
            schema["maximum"] = self._maximum
        if self._enum is not None:
            schema["enum"] = list(self._enum)
        return schema


class NumberSchema(Schema):
    """数值参数：占位值、描述、上下界、枚举、可空。"""

    def __init__(
        self,
        value: float = 0.0,
        *,
        description: str = "",
        minimum: float | None = None,
        maximum: float | None = None,
        enum: tuple[float, ...] | list[float] | None = None,
        nullable: bool = False,
    ) -> None:
        self._value = value
        self._description = description
        self._minimum = minimum
        self._maximum = maximum
        self._enum = tuple(enum) if enum is not None else None
        self._nullable = nullable

    def to_json_schema(self) -> dict[str, Any]:
        schema_type: Any = ["number", "null"] if self._nullable else "number"
        schema: dict[str, Any] = {"type": schema_type}
        if self._description:
            schema["description"] = self._description
        if self._minimum is not None:
            schema["minimum"] = self._minimum
        if self._maximum is not None:
            schema["maximum"] = self._maximum
        if self._enum is not None:
            schema["enum"] = list(self._enum)
        return schema


class BooleanSchema(Schema):
    """布尔参数：描述、默认值、可空。"""

    def __init__(
        self,
        *,
        description: str = "",
        default: bool | None = None,
        nullable: bool = False,
    ) -> None:
        self._description = description
        self._default = default
        self._nullable = nullable

    def to_json_schema(self) -> dict[str, Any]:
        schema_type: Any = ["boolean", "null"] if self._nullable else "boolean"
        schema: dict[str, Any] = {"type": schema_type}
        if self._description:
            schema["description"] = self._description
        if self._default is not None:
            schema["default"] = self._default
        return schema


class ArraySchema(Schema):
    """数组参数：元素 schema、描述、长度范围、可空。"""

    def __init__(
        self,
        items: Any | None = None,
        *,
        description: str = "",
        min_items: int | None = None,
        max_items: int | None = None,
        nullable: bool = False,
    ) -> None:
        self._items_schema: Any = items if items is not None else StringSchema("")
        self._description = description
        self._min_items = min_items
        self._max_items = max_items
        self._nullable = nullable

    def to_json_schema(self) -> dict[str, Any]:
        schema_type: Any = ["array", "null"] if self._nullable else "array"
        schema: dict[str, Any] = {
            "type": schema_type,
            "items": Schema.fragment(self._items_schema),
        }
        if self._description:
            schema["description"] = self._description
        if self._min_items is not None:
            schema["minItems"] = self._min_items
        if self._max_items is not None:
            schema["maxItems"] = self._max_items
        return schema


class ObjectSchema(Schema):
    """对象参数：字段可由 ``properties`` 或关键字参数给出。"""

    def __init__(
        self,
        properties: Mapping[str, Any] | None = None,
        *,
        required: list[str] | None = None,
        description: str = "",
        additional_properties: bool | dict[str, Any] | None = None,
        nullable: bool = False,
        **kwargs: Any,
    ) -> None:
        self._properties = dict(properties or {}, **kwargs)
        self._required = list(required or [])
        self._root_description = description
        self._additional_properties = additional_properties
        self._nullable = nullable

    def to_json_schema(self) -> dict[str, Any]:
        schema_type: Any = ["object", "null"] if self._nullable else "object"
        props = {key: Schema.fragment(value) for key, value in self._properties.items()}
        schema: dict[str, Any] = {"type": schema_type, "properties": props}
        if self._required:
            schema["required"] = self._required
        if self._root_description:
            schema["description"] = self._root_description
        if self._additional_properties is not None:
            schema["additionalProperties"] = self._additional_properties
        return schema


def tool_parameters_schema(
    *,
    required: list[str] | None = None,
    description: str = "",
    **properties: Any,
) -> dict[str, Any]:
    """构造工具根参数：``{"type": "object", "properties": ...}``。"""
    return ObjectSchema(
        required=required,
        description=description,
        **properties,
    ).to_json_schema()


__all__ = [
    "ArraySchema",
    "BooleanSchema",
    "IntegerSchema",
    "NumberSchema",
    "ObjectSchema",
    "StringSchema",
    "tool_parameters_schema",
]
