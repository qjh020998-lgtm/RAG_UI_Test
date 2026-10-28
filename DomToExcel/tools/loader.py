"""工具发现与注册。

参考 nanobot 的 ``agent/tools/loader.py``：
- 扫描包内模块，收集 ``Tool`` 子类；
- 支持 entry_points 插件；
- 按 scope 过滤；
- 调用 ``Tool.create(ctx)`` 创建实例并注册。

插件组名：``dom_to_excel.tools``
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from importlib.metadata import entry_points
from typing import Any

from DomToExcel.tools.base import Tool
from DomToExcel.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_SKIP_MODULES = frozenset({
    "base", "schema", "registry", "context", "loader", "__init__",
})
_PLUGIN_GROUP = "dom_to_excel.tools"


class ToolLoader:
    """工具发现器。"""

    def __init__(
        self,
        package: Any = None,
        *,
        test_classes: list[type[Tool]] | None = None,
        plugin_group: str = _PLUGIN_GROUP,
    ) -> None:
        if package is None:
            import DomToExcel.tools as _pkg
            package = _pkg
        self._package = package
        self._test_classes = test_classes
        self._plugin_group = plugin_group
        self._discovered: list[type[Tool]] | None = None
        self._plugins: dict[str, type[Tool]] | None = None

    def discover(self) -> list[type[Tool]]:
        """扫描包内模块，返回 Tool 子类。"""
        if self._test_classes is not None:
            return list(self._test_classes)
        if self._discovered is not None:
            return self._discovered

        seen: set[int] = set()
        results: list[type[Tool]] = []
        for module_info in pkgutil.iter_modules(self._package.__path__):
            module_name = module_info.name
            if module_name.startswith("_") or module_name in _SKIP_MODULES:
                continue
            try:
                module = importlib.import_module(f".{module_name}", self._package.__name__)
            except Exception:
                logger.exception("导入工具模块失败：%s", module_name)
                continue
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Tool)
                    and attr is not Tool
                    and not attr_name.startswith("_")
                    and not getattr(attr, "__abstractmethods__", None)
                    and getattr(attr, "_plugin_discoverable", True)
                    and id(attr) not in seen
                ):
                    seen.add(id(attr))
                    results.append(attr)
        results.sort(key=lambda cls: cls.__name__)
        self._discovered = results
        return results

    def _discover_plugins(self) -> dict[str, type[Tool]]:
        """发现通过 entry_points 注册的外部工具。"""
        if self._plugins is not None:
            return self._plugins
        plugins: dict[str, type[Tool]] = {}
        try:
            eps = entry_points(group=self._plugin_group)
        except Exception:
            logger.exception("读取工具插件失败：%s", self._plugin_group)
            return plugins
        for ep in eps:
            try:
                cls = ep.load()
                if (
                    isinstance(cls, type)
                    and issubclass(cls, Tool)
                    and not getattr(cls, "__abstractmethods__", None)
                    and getattr(cls, "_plugin_discoverable", True)
                ):
                    plugins[ep.name] = cls
            except Exception:
                logger.exception("加载工具插件失败：%s", ep.name)
        self._plugins = plugins
        return plugins

    def load(self, ctx: Any, registry: ToolRegistry, *, scope: str = "core") -> list[str]:
        """按 scope 加载工具并注册，返回成功注册的工具名。"""
        registered: list[str] = []
        builtin_names: set[str] = set()
        sources = [(self.discover(), False), (self._discover_plugins().values(), True)]

        for source, is_plugin_source in sources:
            for tool_cls in source:
                label = tool_cls.__name__
                try:
                    if scope not in getattr(tool_cls, "_scopes", {"core"}):
                        continue
                    if not tool_cls.enabled(ctx):
                        continue
                    tool = tool_cls.create(ctx)
                    if registry.has(tool.name):
                        if is_plugin_source and tool.name in builtin_names:
                            logger.warning("插件 %s 跳过：与内置工具 %s 重名", label, tool.name)
                        else:
                            logger.warning("工具名冲突，跳过：%s（%s）", tool.name, label)
                        continue
                    registry.register(tool)
                    registered.append(tool.name)
                    if not is_plugin_source:
                        builtin_names.add(tool.name)
                except Exception:
                    logger.exception("注册工具失败：%s", label)
        return registered


__all__ = ["ToolLoader"]
