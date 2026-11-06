"""工具运行时上下文：workspace、ERP 地址、登录态和产物目录。

所有 LangChain 工具都通过 ``get_context()`` 读取当前配置，默认值取自
当前工作目录和环境变量；需要在 Runner/Agent 启动前调用 ``configure()``。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

DEFAULT_BASE_URL = "http://39.101.122.147:3000"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 工具层直接加载项目根目录 .env，保证登录态失效时能读到 ERP 默认账号。
load_dotenv(PROJECT_ROOT / ".env")


@dataclass
class ToolContext:
    """一次运行的工具上下文。"""

    config: dict[str, Any] = field(default_factory=dict)
    workspace: Path = field(default_factory=Path.cwd)
    base_url: str = ""
    storage_state: str = ""
    artifacts: Path | None = None
    run_id: str = "default"
    browser: Any = None

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        self.artifacts = (
            Path(self.artifacts)
            if self.artifacts is not None
            else self.workspace / "artifacts" / self.run_id
        )
        self.artifacts.mkdir(parents=True, exist_ok=True)
        if not self.base_url:
            self.base_url = str(
                self.config.get("base_url")
                or os.getenv("BASE_URL", "")
                or DEFAULT_BASE_URL
            ).rstrip("/")

    def setting(self, key: str, default: Any = None) -> Any:
        """读取 config 中的配置项。"""
        return self.config.get(key, default)


_context: ToolContext | None = None


def configure(**kwargs: Any) -> ToolContext:
    """替换全局工具上下文，供 Runner 在启动时调用。"""
    global _context
    _context = ToolContext(**kwargs)
    return _context


def get_context() -> ToolContext:
    """返回当前工具上下文；首次访问时按默认值创建。"""
    global _context
    if _context is None:
        _context = ToolContext()
    return _context


__all__ = ["DEFAULT_BASE_URL", "ToolContext", "configure", "get_context"]
