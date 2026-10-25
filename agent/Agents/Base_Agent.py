"""Agent 基础设施：配置、模型、日志、调用入口。

三个 Agent（控件获取 / 步骤生成 / 最终编排）都继承 ``BaseAgent``，共享：

- ``.env`` 里的 LLM 配置（``LLM_API_KEY`` / ``LLM_BASE_URL`` / 模型名）
- Litellm 模型实例（快 / 强两档）
- 统一的日志与 ``run()`` 入口
- ``@tool`` 工具注册方式

流式输出
--------
所有 Agent 默认走 ``Runner.run_streamed``：模型的思考（reasoning）和生成
（output_text）会逐 token 打印，工具调用/返回也会实时显示。相关开关：

- ``AGENT_STREAM``：``true``（默认）流式；``false`` 回退到一次性返回
- ``AGENT_SHOW_THINKING``：``true``（默认）打印思考；``false`` 只看生成
- ``AGENT_STREAM_COLOR``：``auto``（默认）/ ``always`` / ``never``

需要自己消费流式事件（例如接到 Web UI）时，用 ``await agent.astream(prompt)``。

为什么用 LitellmModel
---------------------
项目已经用 ``agents``（OpenAI Agents SDK），``LitellmModel`` 可以通过
``dashscope/`` 前缀直接路由到百炼，不需要改 SDK。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal, TextIO

from agents import Agent, ModelSettings, Runner
from agents.extensions.models.litellm_model import LitellmModel
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 只加载一次；已存在的环境变量优先，便于测试时临时覆盖
load_dotenv(PROJECT_ROOT / ".env", override=False)

log = logging.getLogger("agent")
if not log.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _env_bool(name: str, default: bool) -> bool:
    """读取布尔型环境变量；未设置或空值时用默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


@dataclass(frozen=True)
class AgentSettings:
    """从 ``.env`` 读取的 Agent 配置。"""

    api_key: str
    base_url: str
    fast_model: str
    strong_model: str
    temperature: float
    max_tokens: int
    max_turns: int
    stream: bool = True
    show_thinking: bool = True

    @classmethod
    def from_env(cls) -> "AgentSettings":
        return cls(
            api_key=os.getenv("LLM_API_KEY", "").strip(),
            base_url=os.getenv(
                "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).strip(),
            fast_model=os.getenv("LLM_FAST_MODEL", "qwen3.8-flash").strip(),
            strong_model=os.getenv("LLM_STRONG_MODEL", "qwen3.8-max").strip(),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            max_turns=int(os.getenv("AGENT_MAX_TURNS", "8")),
            stream=_env_bool("AGENT_STREAM", True),
            show_thinking=_env_bool("AGENT_SHOW_THINKING", True),
        )

    def require_key(self) -> None:
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY 为空：请在根目录 .env 中填写后重试")


def build_model(strong: bool = False, settings: AgentSettings | None = None) -> LitellmModel:
    """构建 Litellm 模型；``strong=True`` 用 qwen3.8-max，否则用 flash。"""
    settings = settings or AgentSettings.from_env()
    settings.require_key()

    # litellm 的 dashscope provider 会读 DASHSCOPE_API_KEY
    os.environ.setdefault("DASHSCOPE_API_KEY", settings.api_key)

    model = settings.strong_model if strong else settings.fast_model
    return LitellmModel(model=f"dashscope/{model}", api_key=settings.api_key)


StreamKind = Literal["thinking", "content", "tool_call", "tool_output", "error"]


@dataclass(slots=True)
class AgentStreamEvent:
    """Agent 流式输出的一个片段。

    ``kind`` 取值：

    - ``thinking``：模型思考（reasoning）
    - ``content``：模型生成正文
    - ``tool_call``：工具调用（含参数）
    - ``tool_output``：工具返回
    - ``error``：流式过程中的错误
    """

    kind: StreamKind
    text: str = ""
    data: Any = None


_COLOR_CODES = {
    "dim": "\x1b[90m",
    "cyan": "\x1b[36m",
    "green": "\x1b[32m",
    "red": "\x1b[31m",
    "bold": "\x1b[1m",
    "reset": "\x1b[0m",
}


def _truncate(value: Any, limit: int) -> str:
    """把任意值转成文本预览，超长截断。"""
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...（已截断，共 {len(text)} 字符）"


def _usage_tokens(usage: Any) -> tuple[int, int, int]:
    """从 SDK 的 Usage 里取 ``(输入, 输出, 思考)`` token 数。"""
    if usage is None:
        return 0, 0, 0

    def _get(key: str) -> int:
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, 0)
        return int(value or 0)

    output_details = (
        usage.get("output_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "output_tokens_details", None)
    )
    reasoning = 0
    if output_details is not None:
        reasoning = int(
            output_details.get("reasoning_tokens", 0)
            if isinstance(output_details, dict)
            else getattr(output_details, "reasoning_tokens", 0)
        )
    return _get("input_tokens"), _get("output_tokens"), reasoning


class AgentStreamPrinter:
    """把 :class:`AgentStreamEvent` 实时渲染到终端。"""

    def __init__(
        self,
        name: str,
        *,
        stream: TextIO | None = None,
        show_thinking: bool = True,
        color: str | None = None,
    ):
        self.name = name
        self.show_thinking = show_thinking
        self._stream = stream
        self._color_mode = (color or os.getenv("AGENT_STREAM_COLOR", "auto")).strip().lower()
        self._color_enabled = False
        self._t0 = 0.0
        self._first_token_at: float | None = None
        self._section: str | None = None
        self._at_line_start = True

    @property
    def stream(self) -> TextIO:
        return self._stream or sys.stdout

    # -- 生命周期 ---------------------------------------------------------

    def start(self) -> None:
        self._t0 = time.perf_counter()
        self._color_enabled = self._detect_color()
        self._write("\n" + "=" * 72 + "\n", color="bold")
        self._write(f"[Agent] {self.name}\n", color="bold")
        self._write("=" * 72 + "\n", color="bold")

    def finish(
        self,
        output: str | None,
        elapsed: float,
        usage: Any = None,
        *,
        failed: bool = False,
    ) -> None:
        self._end_section()
        if output and not self._at_line_start:
            self._write("\n")

        self._write("-" * 72 + "\n", color="red" if failed else "bold")
        parts = [
            f"[Agent] {self.name} {'失败' if failed else '完成'}",
            f"耗时 {elapsed:.1f}s",
        ]
        if self._first_token_at is not None:
            parts.append(f"首字 {self._first_token_at - self._t0:.2f}s")
        input_tokens, output_tokens, reasoning_tokens = _usage_tokens(usage)
        if input_tokens or output_tokens:
            token_text = f"tokens {input_tokens + output_tokens} (in {input_tokens} / out {output_tokens}"
            if reasoning_tokens:
                token_text += f"，其中思考 {reasoning_tokens}"
            parts.append(token_text + ")")
        self._write(" | ".join(parts) + "\n", color="red" if failed else "green")

    # -- 事件渲染 ---------------------------------------------------------

    def handle(self, event: AgentStreamEvent) -> None:
        if event.kind == "thinking":
            if not event.text:
                return
            if not self.show_thinking:
                self._end_section()
                return
            self._ensure_section("thinking")
            self._write(event.text, color="dim")
            self._mark_token()
        elif event.kind == "content":
            if not event.text:
                return
            self._ensure_section("content")
            self._write(event.text)
            self._mark_token()
        elif event.kind == "tool_call":
            self._end_section()
            self._write(f"[工具调用] {_one_line(event.text)}\n", color="cyan")
        elif event.kind == "tool_output":
            self._end_section()
            text = event.text.replace("\n", "\n           ")
            self._write(f"[工具返回] {text}\n", color="cyan")
        elif event.kind == "error":
            self._end_section()
            self._write(f"[错误] {_one_line(event.text)}\n", color="red")

    # -- 内部工具 ---------------------------------------------------------

    def _ensure_section(self, section: str) -> None:
        if self._section == section:
            return
        if self._section is not None:
            self._end_section()
        self._section = section
        if section == "thinking":
            self._write("[思考] ", color="dim")
        else:
            self._write("[生成] ", color="green")

    def _end_section(self) -> None:
        if self._section is None:
            return
        if not self._at_line_start:
            self._write("\n")
        self._section = None

    def _mark_token(self) -> None:
        if self._first_token_at is None:
            self._first_token_at = time.perf_counter()

    def _detect_color(self) -> bool:
        if os.getenv("NO_COLOR"):
            return False
        if self._color_mode in ("0", "false", "never", "off"):
            return False
        if self._color_mode in ("1", "true", "always", "on"):
            return True
        try:
            return bool(self.stream.isatty())
        except Exception:
            return False

    def _write(self, text: str, *, color: str | None = None) -> None:
        if not text:
            return
        ends_line = text.endswith("\n")
        if self._color_enabled and color in _COLOR_CODES:
            text = f"{_COLOR_CODES[color]}{text}{_COLOR_CODES['reset']}"
        try:
            self.stream.write(text)
            self.stream.flush()
        except UnicodeEncodeError:
            encoding = getattr(self.stream, "encoding", None) or "utf-8"
            safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            self.stream.write(safe)
            self.stream.flush()
        self._at_line_start = ends_line


def _one_line(text: str) -> str:
    return str(text).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


class BaseAgent:
    """所有 Agent 的基类：统一模型、日志、调用入口。

    子类只需定义 ``name`` / ``instructions``，需要工具时在 ``__init__``
    里传 ``tools=[...]``。
    """

    name = "base"
    instructions = "You are a helpful assistant."

    def __init__(
        self,
        *,
        strong: bool = False,
        tools=None,
        model_settings: ModelSettings | None = None,
    ):
        self.settings = AgentSettings.from_env()
        self.model = build_model(strong=strong, settings=self.settings)
        self.tools = list(tools or [])
        self.model_settings = model_settings or ModelSettings(
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
            include_usage=True,
        )
        self.agent = Agent(
            name=self.name,
            instructions=self.instructions,
            model=self.model,
            model_settings=self.model_settings,
            tools=self.tools,
        )
        self.last_result: Any = None
        self.last_usage: Any = None
        self.last_elapsed = 0.0

    # -- 对外入口 ---------------------------------------------------------

    def run(self, prompt: str, *, stream: bool | None = None) -> str:
        """同步执行一次 Agent 任务，返回最终文本。

        ``stream=None`` 时读取 ``AGENT_STREAM``（默认开）；``stream=False``
        回退到一次性调用，不打印思考和生成过程。
        """
        enabled = self.settings.stream if stream is None else stream
        if not enabled:
            return self._run_blocking(prompt)
        return self._run_streaming(prompt)

    async def arun(self, prompt: str, *, stream: bool | None = None) -> str:
        """异步执行一次 Agent 任务；适合已经在事件循环里的调用方。"""
        enabled = self.settings.stream if stream is None else stream
        if not enabled:
            return await self._run_blocking_async(prompt)
        return await self._consume_stream(prompt)

    async def astream(self, prompt: str) -> AsyncIterator[AgentStreamEvent]:
        """异步流式执行，逐个产出思考 / 生成 / 工具事件。

        调用方负责渲染；``self.last_result`` 在流结束后可拿到
        ``RunResultStreaming``，``self.last_usage`` 是 token 用量。
        """
        log.info("[Agent] %s 被调用（流式）", self.name)
        self.last_result = None
        self.last_usage = None
        result = None
        try:
            result = Runner.run_streamed(
                self.agent,
                prompt,
                max_turns=self.settings.max_turns,
            )
            self.last_result = result
            async for event in result.stream_events():
                for chunk in self._translate_event(event):
                    yield chunk
        except Exception as exc:  # noqa: BLE001 - 需要把异常也透传给渲染器
            log.error(
                "[Agent] %s 执行失败：%s",
                self.name,
                exc,
                exc_info=log.isEnabledFor(logging.DEBUG),
            )
            yield AgentStreamEvent("error", f"{type(exc).__name__}: {exc}")
            raise
        finally:
            if result is not None:
                try:
                    self.last_usage = result.context_wrapper.usage
                except Exception:  # pragma: no cover - 中断时 usage 可能不可用
                    self.last_usage = None

    # -- 内部实现 ---------------------------------------------------------

    def _run_streaming(self, prompt: str) -> str:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._consume_stream(prompt))
        raise RuntimeError(
            "当前线程已有运行中的事件循环，请改用 `await agent.arun(prompt)`"
        )

    async def _consume_stream(self, prompt: str) -> str:
        printer = AgentStreamPrinter(
            self.name,
            show_thinking=self.settings.show_thinking,
        )
        printer.start()
        started = time.perf_counter()
        try:
            async for event in self.astream(prompt):
                printer.handle(event)
        except Exception:
            printer.finish(
                None,
                time.perf_counter() - started,
                self.last_usage,
                failed=True,
            )
            raise

        elapsed = time.perf_counter() - started
        self.last_elapsed = elapsed
        output = ""
        if self.last_result is not None:
            output = (self.last_result.final_output or "").strip()
        printer.finish(output, elapsed, self.last_usage)
        log.info(
            "[Agent] %s 完成（输出 %s 字符，耗时 %.1fs）",
            self.name,
            len(output),
            elapsed,
        )
        return output

    def _run_blocking(self, prompt: str) -> str:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._run_blocking_async(prompt))
        raise RuntimeError(
            "当前线程已有运行中的事件循环，请改用 `await agent.arun(prompt)`"
        )

    async def _run_blocking_async(self, prompt: str) -> str:
        log.info("[Agent] %s 被调用", self.name)
        started = time.perf_counter()
        result = await Runner.run(
            self.agent,
            prompt,
            max_turns=self.settings.max_turns,
        )
        self.last_result = result
        self.last_usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
        elapsed = time.perf_counter() - started
        self.last_elapsed = elapsed
        output = (result.final_output or "").strip()
        log.info(
            "[Agent] %s 完成（输出 %s 字符，耗时 %.1fs）",
            self.name,
            len(output),
            elapsed,
        )
        return output

    @staticmethod
    def _translate_event(event: Any) -> list[AgentStreamEvent]:
        """把 SDK 事件翻译成 Agent 层统一事件。"""
        if isinstance(event, RawResponsesStreamEvent):
            data = event.data
            event_type = getattr(data, "type", "")

            if event_type in (
                "response.reasoning_summary_text.delta",
                "response.reasoning_text.delta",
            ):
                delta = getattr(data, "delta", "") or ""
                return [AgentStreamEvent("thinking", delta)] if delta else []

            if event_type in ("response.output_text.delta", "response.refusal.delta"):
                delta = getattr(data, "delta", "") or ""
                return [AgentStreamEvent("content", delta)] if delta else []

            if event_type == "error":
                message = (
                    getattr(data, "message", None)
                    or getattr(data, "code", None)
                    or "模型流式响应错误"
                )
                return [AgentStreamEvent("error", str(message))]

            if event_type == "response.failed":
                response = getattr(data, "response", None)
                error = getattr(response, "error", None)
                return [AgentStreamEvent("error", str(error or "模型响应失败"))]

            # 工具参数的 token 级增量不打印，等 tool_called 事件拿到完整参数
            return []

        if isinstance(event, RunItemStreamEvent):
            item = event.item
            if event.name == "tool_called":
                tool_name = getattr(item, "tool_name", None)
                raw = getattr(item, "raw_item", None)
                if not tool_name:
                    if isinstance(raw, dict):
                        tool_name = raw.get("name")
                    else:
                        tool_name = getattr(raw, "name", None)
                arguments = (
                    raw.get("arguments", "")
                    if isinstance(raw, dict)
                    else getattr(raw, "arguments", "")
                )
                text = f"{tool_name or 'tool'}({_truncate(arguments, 500)})"
                return [AgentStreamEvent("tool_call", text, item)]

            if event.name == "tool_output":
                output = getattr(item, "output", None)
                if output is None:
                    raw = getattr(item, "raw_item", None)
                    output = (
                        raw.get("output")
                        if isinstance(raw, dict)
                        else getattr(raw, "output", "")
                    )
                return [AgentStreamEvent("tool_output", _truncate(output, 800), item)]

            # message_output_created / reasoning_item_created 的内容已经
            # 通过上面的 delta 打印过，这里不再重复整段输出
            return []

        return []


if __name__ == "__main__":
    # 自检：确认 .env 与模型可用
    settings = AgentSettings.from_env()
    print("api_key set:", bool(settings.api_key))
    print("fast:", settings.fast_model, "| strong:", settings.strong_model)
    print("max_turns:", settings.max_turns, "| stream:", settings.stream)
