"""工具注册表。

完整调用链：
    查找工具 -> 参数转换 -> 参数校验 -> 执行 -> 结果归一化 -> 返回

参考 nanobot 的 ``agent/tools/registry.py``，但去掉了 MCP 相关逻辑。
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from DomToExcel.tools.base import Tool

_ERROR_HINT = "\n\n[请根据上面的错误调整参数后重试。]"

_BOOL_TRUE = {"true", "1", "yes", "y", "on"}
_BOOL_FALSE = {"false", "0", "no", "n", "off"}


class ToolRegistry:
    """工具注册表：负责查找、转换、校验、执行和结果返回。"""

    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._definitions: list[dict[str, Any]] | None = None
        for tool in tools or []:
            self.register(tool)

    # ------------------------------------------------------------------
    # 注册 / 查找
    # ------------------------------------------------------------------

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        """注册工具；默认不允许重名，避免静默覆盖。"""
        if not isinstance(tool, Tool):
            raise TypeError(f"只能注册 Tool 实例，收到 {type(tool).__name__}")
        if not tool.name:
            raise ValueError("工具 name 不能为空")
        if tool.name in self._tools and not replace:
            raise ValueError(f"工具已存在：{tool.name}；如要覆盖请传 replace=True")
        errors = tool.definition_errors()
        if errors:
            raise ValueError(f"工具 {tool.name} 定义不合法：" + "；".join(errors))
        self._tools[tool.name] = tool
        self._definitions = None

    def unregister(self, name: str) -> bool:
        """注销工具，返回是否真的删除了。"""
        removed = self._tools.pop(name, None) is not None
        if removed:
            self._definitions = None
        return removed

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """返回给模型的 function schema 列表；按名字排序，结果缓存。"""
        if self._definitions is None:
            definitions = [tool.to_schema() for tool in self._tools.values()]
            definitions.sort(key=lambda item: item["function"]["name"])
            self._definitions = definitions
        return self._definitions

    # ------------------------------------------------------------------
    # 准备调用：查找 -> 转换 -> 校验
    # ------------------------------------------------------------------

    def prepare_call(
        self,
        name: str,
        params: Any,
    ) -> tuple[Tool | None, Any, str | None]:
        """查找工具、转换参数、校验参数。

        返回 ``(tool, cast_params, error)``；error 为 None 表示可以执行。
        """
        tool = self.get(name)
        if tool is None:
            suggestion = self._suggest_name(name)
            hint = f"，是否想调用 {suggestion}？" if suggestion else ""
            return None, params, (
                f"工具不存在：{name}{hint}；可用工具：{', '.join(self.tool_names)}"
            )

        params = self._coerce_params(tool, params)
        if not isinstance(params, dict):
            return tool, params, (
                f"工具 {name} 的参数必须是 JSON object，实际是 {type(params).__name__}"
            )

        params = self._cast_params(params, tool.json_schema())
        errors = tool.validate_params(params)
        if errors:
            return tool, params, f"工具 {name} 参数不合法：" + "；".join(errors)
        return tool, params, None

    # ------------------------------------------------------------------
    # 执行：准备 -> 调用 -> 结果归一化
    # ------------------------------------------------------------------

    async def execute(self, name: str, params: Any) -> str:
        """执行工具，始终返回字符串（成功内容或错误信息）。"""
        tool, prepared, error = self.prepare_call(name, params)
        if error:
            return error + _ERROR_HINT
        assert tool is not None

        try:
            result = await tool.execute(**prepared)
        except Exception as exc:  # noqa: BLE001 - 工具异常回给模型
            return f"工具 {name} 执行失败：{type(exc).__name__}: {exc}" + _ERROR_HINT
        return self._normalize_result(result)

    # ------------------------------------------------------------------
    # 参数转换
    # ------------------------------------------------------------------

    @classmethod
    def _coerce_params(cls, tool: Tool, params: Any) -> Any:
        params = cls._coerce_argument_value(params)
        return cls._unwrap_arguments_payload(tool, params)

    @classmethod
    def _coerce_argument_value(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return {}
        if not text.startswith(("{", "[")):
            return value
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value

    @classmethod
    def _unwrap_arguments_payload(cls, tool: Tool, params: Any) -> Any:
        """兼容模型返回 ``{"arguments": {...}}`` 的包装。"""
        if not isinstance(params, dict) or set(params) != {"arguments"}:
            return params
        properties = tool.json_schema().get("properties", {})
        if isinstance(properties, dict) and "arguments" in properties:
            return params
        return cls._coerce_argument_value(params["arguments"])

    @classmethod
    def _cast_params(cls, params: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return params
        return {
            key: cls._cast_value(value, properties.get(key, {}))
            for key, value in params.items()
        }

    @classmethod
    def _cast_value(cls, value: Any, schema: dict[str, Any]) -> Any:
        if value is None or not isinstance(schema, dict):
            return value

        raw_type = schema.get("type")
        expected = next((t for t in raw_type if t != "null"), None) if isinstance(raw_type, list) else raw_type

        if expected == "integer":
            if isinstance(value, bool):
                return value
            if isinstance(value, int):
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, str):
                try:
                    return int(value.strip())
                except ValueError:
                    return value
        elif expected == "number":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value.strip())
                except ValueError:
                    return value
        elif expected == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                text = value.strip().lower()
                if text in _BOOL_TRUE:
                    return True
                if text in _BOOL_FALSE:
                    return False
        elif expected == "string":
            if isinstance(value, (dict, list)):
                return value
            return value if isinstance(value, str) else str(value)
        elif expected == "array" and isinstance(value, list):
            items = schema.get("items")
            if isinstance(items, dict):
                return [cls._cast_value(item, items) for item in value]
        elif expected == "object" and isinstance(value, dict):
            properties = schema.get("properties", {})
            if isinstance(properties, dict):
                return {
                    key: cls._cast_value(item, properties.get(key, {}))
                    for key, item in value.items()
                }
        return value

    # ------------------------------------------------------------------
    # 结果与建议
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_result(result: Any) -> str:
        if result is None:
            return "(无输出)"
        if isinstance(result, str):
            return result if result.strip() else "(无输出)"
        try:
            return json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(result)

    def _suggest_name(self, name: str) -> str | None:
        key = "".join(ch.lower() for ch in str(name) if ch.isalnum())
        if not key:
            return None
        matches = [
            registered
            for registered in self._tools
            if "".join(ch.lower() for ch in registered if ch.isalnum()) == key
        ]
        return matches[0] if len(matches) == 1 else None


__all__ = ["ToolRegistry"]
