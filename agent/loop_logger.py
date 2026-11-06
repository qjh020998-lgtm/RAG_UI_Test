"""Agent 循环日志：记录模型调用、工具调用、结果和 token 用量。

通过 LangChain callback 挂到 BaseAgent 的 graph 上，不依赖库的 DEBUG 日志。
"""
from __future__ import annotations

import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from utils.logger import get_logger

log = get_logger(__name__)

_MAX_TEXT = 300


def shorten(value: Any, limit: int = _MAX_TEXT) -> str:
    """压成单行并截断，避免把整段 prompt / 工具结果写进日志。"""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"...(+{len(text) - limit})"


def _message_text(message: Any) -> str:
    """兼容 ``text`` 是属性或方法的 langchain-core 版本。"""
    text = getattr(message, "text", None)
    if callable(text):
        try:
            text = text()
        except Exception:  # noqa: BLE001
            text = None
    if text is None:
        text = getattr(message, "content", "")
    return str(text or "")


class AgentLoopLogger(BaseCallbackHandler):
    """把一次 Agent 运行的关键节点写成一行一条的日志。"""

    def __init__(self, agent: str = "Agent") -> None:
        self.agent = agent
        self._model_calls = 0
        self._tool_calls = 0
        self._tool_names: dict[Any, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 模型
    # ------------------------------------------------------------------

    def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        with self._lock:
            self._model_calls += 1
            call_no = self._model_calls

        batch = messages[0] if messages and isinstance(messages[0], (list, tuple)) else messages
        last = batch[-1] if batch else None
        last_role = getattr(last, "type", "?")
        last_text = shorten(getattr(last, "content", ""), 160)
        log.info(
            "[%s] model#%d start | messages=%d | last=%s:%s",
            self.agent,
            call_no,
            len(batch),
            last_role,
            last_text,
        )

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        with self._lock:
            call_no = self._model_calls

        message = None
        try:
            message = response.generations[0][0].message
        except (AttributeError, IndexError, TypeError):
            pass

        if message is None:
            log.info("[%s] model#%d end", self.agent, call_no)
            return

        tool_calls = getattr(message, "tool_calls", None) or []
        names = [str(call.get("name", "?")) for call in tool_calls]
        usage = getattr(message, "usage_metadata", None) or {}
        finish = (getattr(message, "response_metadata", None) or {}).get("finish_reason")
        usage_text = (
            f"in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}"
            if usage
            else "-"
        )

        if names:
            log.info(
                "[%s] model#%d end | finish=%s | tool_calls=%s | usage=%s",
                self.agent,
                call_no,
                finish,
                ",".join(names),
                usage_text,
            )
        else:
            content = _message_text(message)
            log.info(
                "[%s] model#%d end | final | finish=%s | content=%s | usage=%s",
                self.agent,
                call_no,
                finish,
                shorten(content, 200),
                usage_text,
            )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        with self._lock:
            call_no = self._model_calls
        log.error(
            "[%s] model#%d error | %s: %s",
            self.agent,
            call_no,
            type(error).__name__,
            error,
        )

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        with self._lock:
            self._tool_calls += 1
            call_no = self._tool_calls

        name = ""
        if isinstance(serialized, dict):
            name = str(serialized.get("name") or "")
        name = name or str(kwargs.get("name") or "tool")
        self._tool_names[run_id] = name

        log.info(
            "[%s] tool#%d start | %s | args=%s",
            self.agent,
            call_no,
            name,
            shorten(input_str),
        )

    def on_tool_end(self, output: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        name = self._tool_names.pop(run_id, "tool")
        content = getattr(output, "content", output)
        log.info(
            "[%s] tool end | %s | result=%s",
            self.agent,
            name,
            shorten(content),
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        name = self._tool_names.pop(run_id, "tool")
        log.error(
            "[%s] tool error | %s | %s: %s",
            self.agent,
            name,
            type(error).__name__,
            error,
        )
