"""工具运行时上下文。

结构参考 nanobot 的 ``agent/tools/context.py``：
- ``RequestContext``：单次请求的轻量标识；
- ``ContextAware``：需要感知请求上下文的工具协议；
- ContextVar：绑定/读取/重置当前请求；
- ``ToolContext``：工具构造时的依赖容器。

单 Agent 用不到 channel/bus/subagent/cron 等字段，但先保留为可选字段，
方便后续和 nanobot 的工具实现保持结构一致；实际只强依赖 config/workspace。
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

_CURRENT_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "dom_to_excel_tool_request_context",
    default=None,
)


@dataclass(frozen=True)
class RequestContext:
    """单次请求上下文。

    单 Agent 默认 channel=cli、chat_id=default；并发跑多会话时再填 session_key。
    """

    channel: str = "cli"
    chat_id: str = "default"
    message_id: str | None = None
    session_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ContextAware(Protocol):
    """需要感知当前请求上下文的工具实现该协议。"""

    def set_context(self, ctx: RequestContext) -> None:
        ...


def bind_request_context(ctx: RequestContext) -> Token[RequestContext | None]:
    """绑定当前请求上下文，返回用于重置的 token。"""
    return _CURRENT_REQUEST_CONTEXT.set(ctx)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    """恢复绑定前的请求上下文。"""
    _CURRENT_REQUEST_CONTEXT.reset(token)


def current_request_context() -> RequestContext | None:
    """读取当前请求上下文。"""
    return _CURRENT_REQUEST_CONTEXT.get()


def current_request_session_key() -> str | None:
    """读取当前请求的 session_key。"""
    ctx = current_request_context()
    return ctx.session_key if ctx else None


@dataclass
class ToolContext:
    """工具构造时的依赖容器。

    前两个字段和 nanobot 保持一致：``config``、``workspace``。
    其余字段都有默认值，单 Agent 只传需要的即可。
    """

    config: Any
    workspace: Path | str

    # 以下字段为与 nanobot 结构对齐而保留，单 Agent 默认为 None
    bus: Any | None = None
    subagent_manager: Any | None = None
    cron_service: Any | None = None
    sessions: Any | None = None
    file_state_store: Any = None
    provider_snapshot_loader: Callable[[], Any] | None = None
    image_generation_provider_configs: dict[str, Any] | None = None
    timezone: str = "Asia/Shanghai"
    workspace_sandbox: Any | None = None
    runtime_events: Any | None = None

    # 本项目专用字段
    run_id: str = "default"
    base_url: str = ""
    storage_state: str = ""
    artifacts: Path | None = None
    browser: Any | None = None

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)
        if self.artifacts is None:
            self.artifacts = self.workspace / "artifacts" / self.run_id
        else:
            self.artifacts = Path(self.artifacts)
        self.artifacts.mkdir(parents=True, exist_ok=True)


__all__ = [
    "ContextAware",
    "RequestContext",
    "ToolContext",
    "bind_request_context",
    "current_request_context",
    "current_request_session_key",
    "reset_request_context",
]
