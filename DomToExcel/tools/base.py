"""最小化的 Schema 与 Tool 基类。

参考 nanobot 的 ``agent/tools/base.py``。
当前保留抽象 ``Schema``、``Tool`` 和一个轻量的 ``tool_parameters`` 装饰器；
具体 Schema 子类后续统一编写，不引入注册表，也不强制异步。
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from copy import deepcopy
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from DomToExcel.tools.context import ToolContext

_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}

_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


class Schema(ABC):
    """抽象 JSON Schema 片段。"""

    @staticmethod
    def validate_json_schema(value: Any, schema: dict[str, Any], path: str = "") -> list[str]:
        """按 JSON Schema 子集校验一个值。

        支持字段：type、enum、minimum、maximum、minLength、maxLength、
        required、properties、items、minItems、maxItems。
        返回错误信息列表；空列表表示校验通过。
        """
        errors: list[str] = []
        label = path or "parameter"
        if not isinstance(schema, dict):
            return [f"{label} has invalid schema"]

        raw_type = schema.get("type")
        nullable = (isinstance(raw_type, list) and "null" in raw_type) or schema.get("nullable", False)
        expected = next((t for t in raw_type if t != "null"), None) if isinstance(raw_type, list) else raw_type

        if nullable and value is None:
            return []
        if expected == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                return [f"{label} should be integer"]
        elif expected == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return [f"{label} should be number"]
        elif expected in _JSON_TYPES and not isinstance(value, _JSON_TYPES[expected]):
            return [f"{label} should be {expected}"]

        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")
        if expected in ("integer", "number"):
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")
        if expected == "string":
            if "minLength" in schema and len(value) < schema["minLength"]:
                errors.append(f"{label} must be at least {schema['minLength']} chars")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                errors.append(f"{label} must be at most {schema['maxLength']} chars")
        if expected == "object":
            props = schema.get("properties", {})
            for key in schema.get("required", []):
                if key not in value:
                    errors.append(f"missing required {path + '.' if path else ''}{key}")
            for key, item in value.items():
                if key in props:
                    child = f"{path}.{key}" if path else key
                    errors.extend(Schema.validate_json_schema(item, props[key], child))
        if expected == "array":
            if "minItems" in schema and len(value) < schema["minItems"]:
                errors.append(f"{label} must have at least {schema['minItems']} items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errors.append(f"{label} must have at most {schema['maxItems']} items")
            if "items" in schema:
                for index, item in enumerate(value):
                    child = f"{path}[{index}]" if path else f"[{index}]"
                    errors.extend(Schema.validate_json_schema(item, schema["items"], child))
        return errors

    @abstractmethod
    def to_json_schema(self) -> dict[str, Any]:
        ...

    def validate_value(self, value: Any, path: str = "") -> list[str]:
        return Schema.validate_json_schema(value, self.to_json_schema(), path)

    @staticmethod
    def fragment(value: Schema | dict[str, Any]) -> dict[str, Any]:
        if isinstance(value, Schema):
            return value.to_json_schema()
        if isinstance(value, dict):
            return value
        raise TypeError(f"Expected Schema or dict, got {type(value).__name__}")


class Tool(ABC):
    """最小化的 Agent 工具。

    子类必须定义 ``name``、``description``、``parameters`` 三个属性。
    ``parameters`` 必须是 object 类型的 JSON Schema。

    并发约定（与 nanobot 一致）：
    - ``read_only = True``：只读、无副作用，可并行；
    - ``exclusive = True``：必须独占执行，即使只读也不能并行；
    - ``concurrency_safe``：由 ``read_only and not exclusive`` 推导，不用手写。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名，供模型调用。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具说明，供模型判断何时调用。"""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any] | Schema:
        """参数 JSON Schema，必须是 object 类型。"""
        ...

    @property
    def read_only(self) -> bool:
        """是否只读、无副作用。"""
        return False

    @property
    def exclusive(self) -> bool:
        """是否必须独占执行。"""
        return False

    @property
    def concurrency_safe(self) -> bool:
        """是否允许与其他工具并行执行。"""
        return self.read_only and not self.exclusive

    @classmethod
    def enabled(cls, ctx: ToolContext | None = None) -> bool:
        """是否允许被 Loader 加载；子类可覆盖。"""
        return True

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        """由 Loader 创建实例；默认把 ToolContext 传给构造函数。"""
        return cls()

    def json_schema(self) -> dict[str, Any]:
        return Schema.fragment(self.parameters)

    def definition_errors(self) -> list[str]:
        """检查 name/description/parameters 是否严格合法。"""
        errors: list[str] = []

        name = self.name
        if not isinstance(name, str) or not name.strip():
            errors.append("name 不能为空")
        elif not _TOOL_NAME.fullmatch(name):
            errors.append("name 必须匹配 ^[A-Za-z_][A-Za-z0-9_-]{0,63}$")

        description = self.description
        if not isinstance(description, str) or not description.strip():
            errors.append("description 不能为空")

        try:
            schema = self.json_schema()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"parameters 无法解析：{exc}")
            return errors
        if not isinstance(schema, dict):
            errors.append("parameters 必须是 dict")
        else:
            if schema.get("type") != "object":
                errors.append("parameters.type 必须是 object")
            props = schema.get("properties")
            if not isinstance(props, dict):
                errors.append("parameters.properties 必须是 dict")
            required = schema.get("required", [])
            if not isinstance(required, list):
                errors.append("parameters.required 必须是 list")
            elif isinstance(props, dict):
                missing = [key for key in required if key not in props]
                if missing:
                    errors.append(f"required 中的字段未在 properties 定义：{missing}")
            try:
                json.dumps(schema)
            except (TypeError, ValueError) as exc:
                errors.append(f"parameters 必须可 JSON 序列化：{exc}")
        return errors

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        if not isinstance(params, dict):
            return [f"parameters must be an object, got {type(params).__name__}"]
        return Schema.validate_json_schema(params, self.json_schema())

    def to_schema(self) -> dict[str, Any]:
        errors = self.definition_errors()
        if errors:
            raise ValueError("工具定义不合法：" + "；".join(errors))
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.json_schema(),
            },
        }

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """执行工具，返回字符串、字典或列表。"""
        raise NotImplementedError


_ToolT = TypeVar("_ToolT", bound=Tool)


def tool_parameters(schema: dict[str, Any]) -> Callable[[type[_ToolT]], type[_ToolT]]:
    """类装饰器：把 JSON Schema 注入为 ``parameters`` 属性。

    和 nanobot 的 ``tool_parameters`` 用法一致，省略了手动写
    ``@property def parameters`` 的样板代码。

    示例::

        @tool_parameters({
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        })
        class ReadFileTool(Tool):
            name = "read_file"
            description = "读取文件"
            def execute(self, path: str) -> str:
                ...
    """
    if not isinstance(schema, dict):
        raise TypeError("schema 必须是 dict")

    def decorator(cls: type[_ToolT]) -> type[_ToolT]:
        if not issubclass(cls, Tool):
            raise TypeError("@tool_parameters 只能用于 Tool 子类")
        frozen = deepcopy(schema)

        @property
        def parameters(self: Any) -> dict[str, Any]:
            return deepcopy(frozen)

        cls.parameters = parameters  # type: ignore[assignment]
        abstract = getattr(cls, "__abstractmethods__", None)
        if abstract and "parameters" in abstract:
            cls.__abstractmethods__ = frozenset(abstract - {"parameters"})
        return cls

    return decorator


# 兼容单数写法
tool_parameter = tool_parameters
