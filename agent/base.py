"""Agent 基类：固定类型与提示词，模型和工具由具体 Agent 传入。

ReAct 循环交给 LangGraph ``create_react_agent``；本模块负责把
``recursion_limit``（graph 步数）传给 graph，并提供整体超时、流式事件
和最终回答的后处理钩子。
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Sequence
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
)
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent

from agent.loop_logger import AgentLoopLogger, shorten
from context import ContextBuilder
from utils.logger import get_logger

logger = get_logger(__name__)


class AgentTimeoutError(RuntimeError):
    """Agent 整体执行超过 AGENT_TIMEOUT。"""


def _load_timeout() -> float:
    raw = (os.getenv("AGENT_TIMEOUT") or "").strip()
    if not raw:
        return 240.0
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"AGENT_TIMEOUT 必须是数字，当前值：{raw!r}") from exc
    if value <= 0:
        raise ValueError(f"AGENT_TIMEOUT 必须大于 0，当前值：{value}")
    return value


# ContextBuilder 返回 OpenAI 风格消息，交给 LangGraph 前转成 LangChain 消息。
_MESSAGE_TYPES = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


def _to_langchain_messages(messages: Sequence[dict[str, str]]) -> list[Any]:
    converted = []
    for message in messages:
        message_type = _MESSAGE_TYPES.get(message.get("role", "user"), HumanMessage)
        converted.append(message_type(content=message.get("content", "")))
    return converted


class AgentType(str, Enum):
    """ERP 测试平台的 Agent 类型。"""

    PAGE = "page"          # 页面交互：采集控件、UI 操作、HTTP 调用
    CASE = "case"          # 用例生成：知识检索 + 用例生成 + xlsx
    CODE = "code"          # 代码生成：用例 -> pytest + Playwright 代码
    GENERAL = "general"    # 通用对话：与 ERP 测试无关的问题


SKILLS_DIR = Path(__file__).resolve().parents[1] / "Skills"

# 每类 Agent 的技能文件（相对 Skills/ 目录），拼进 system prompt。
SKILL_FILES: dict[AgentType, str] = {
    AgentType.PAGE: "page/SKILL.md",
    AgentType.CASE: "case_generation/SKILL.md",
    AgentType.CODE: "code_generation/SKILL.md",
    AgentType.GENERAL: "general/SKILL.md",
}


@lru_cache(maxsize=None)
def _read_skill(relative_path: str) -> str:
    """读取技能文件；文件缺失时告警并返回空串。"""
    if not relative_path:
        return ""
    path = SKILLS_DIR / relative_path
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("读取技能文件失败：%s（%s）", path, exc)
        return ""


COMMON_RULES = """## 通用规则
1. 先调用工具获取事实，再下结论，不要编造页面元素、接口返回或用例数据。
2. 工具报错时阅读错误信息并调整参数后重试，不要原样重复失败的调用。
3. 任务完成后用中文简要说明结果、关键证据和产物路径。
4. 同一轮只发起一个工具调用，拿到结果后再决定下一步，避免并发操作页面。"""


class BaseAgent:
    """所有 ERP 测试 Agent 的基类。

    子类只需固定 ``agent_type`` 和 ``system_prompt``；
    模型和工具由构造参数传入，BaseAgent 不定义具体有哪些工具。
    """

    agent_type: AgentType
    system_prompt: str = ""

    def __init__(
        self,
        llm: BaseChatModel,
        tools: Sequence[BaseTool] | None = None,
        context_builder: ContextBuilder | None = None,
        timeout: float | None = None,
    ) -> None:
        if not isinstance(getattr(self, "agent_type", None), AgentType):
            raise TypeError(f"{type(self).__name__} 必须定义 agent_type: AgentType")
        if llm is None:
            raise ValueError("llm 不能为空")
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout 必须大于 0")

        self.llm = llm
        self.tools: list[BaseTool] = list(tools or [])
        self.timeout = timeout if timeout is not None else _load_timeout()
        # 跨轮历史、用户画像和情景记忆统一由 ContextBuilder 负责；
        # LangGraph 图不挂 checkpointer，只作为单轮无状态执行器。
        self.context = context_builder or ContextBuilder()
        self._agent = create_react_agent(
            self.llm,
            tools=self.tools,
        )


    def get_tools(self) -> list[BaseTool]:
        """返回当前 Agent 可用的工具列表。"""
        return list(self.tools)

    def build_system_prompt(self) -> str:
        """角色说明 + Skills/ 技能文件 + 通用规则。"""
        relative_path = SKILL_FILES.get(self.agent_type, "")
        skill = _read_skill(relative_path)
        if not skill:
            logger.warning("AgentType %s 没有可用的技能文件", self.agent_type)

        parts = [self.system_prompt, skill, COMMON_RULES]
        return "\n\n".join(part.strip() for part in parts if part and part.strip())

    def _check_finish_reason(self, final: AIMessage) -> None:
        """撞到 max_tokens 时正文可能为空，这种结果不能当成成功返回。"""
        finish_reason = (
            getattr(final, "response_metadata", None) or {}
        ).get("finish_reason")
        if finish_reason != "length":
            return

        detail = shorten(getattr(final, "content", ""), 200)
        hint = (
            "模型输出被 max_tokens 截断（finish_reason=length），"
            "请调大 LLM_MAX_TOKENS 或减少单次生成条数"
        )
        logger.error(
            "[%s] %s | 已输出片段：%s",
            type(self).__name__,
            hint,
            detail or "(空)",
        )
        raise RuntimeError(f"{hint}；已输出片段：{detail or '空'}")

    def _postprocess_final(self, content: str, task: str) -> str:
        """最终回答的后处理钩子；默认不做修改。"""
        return content

    def _memory_content(self, content: str) -> str:
        """写入工作记忆的内容；默认保存完整回答。"""
        return content

    async def _build_context(
        self,
        task: str,
        *,
        user_id: str | None,
        thread_id: str | None,
    ) -> tuple[list[dict[str, str]], str | None]:
        """用三层记忆构建本轮消息，返回 ``(messages, session_id)``。

        两者都不传时不做记忆读写，退化为「系统提示词 + 任务」的单轮输入。
        """
        system_prompt = self.build_system_prompt()
        if not (user_id or thread_id):
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ], None

        user_id = (user_id or "").strip() or (thread_id or "").strip() or "default"
        session_id = (thread_id or "").strip() or user_id
        messages = await self.context.build(
            task,
            user_id=user_id,
            session_id=session_id,
            system_prompt=system_prompt,
        )
        return messages, session_id

    async def run(
        self,
        task: str,
        *,
        recursion_limit: int = 20,
        thread_id: str | None = None,
        user_id: str | None = None,
    ) -> AIMessage:
        """执行一次任务，返回最终 AIMessage。

        ``recursion_limit`` 是 LangGraph 的最大 graph 步数；
        ``thread_id`` 是会话 id（工作记忆/情景记忆），``user_id`` 是画像隔离键，
        两者都不传时退化为无记忆的单轮执行。
        """
        if not task or not task.strip():
            raise ValueError("task 不能为空")

        messages, session_id = await self._build_context(
            task, user_id=user_id, thread_id=thread_id
        )
        label = type(self).__name__
        started = time.perf_counter()
        logger.info("[%s] task start | %s", label, shorten(task, 160))

        config: dict[str, Any] = {
            "callbacks": [AgentLoopLogger(agent=label)],
            "recursion_limit": recursion_limit,
        }

        try:
            async with asyncio.timeout(self.timeout):
                result = await self._agent.ainvoke(
                    {"messages": _to_langchain_messages(messages)},
                    config=config,
                )
        except TimeoutError as exc:
            logger.error(
                "[%s] task timeout | %.1fs | limit=%ss",
                label,
                time.perf_counter() - started,
                self.timeout,
            )
            raise AgentTimeoutError(
                f"{label} 执行超过 {self.timeout:g}s，已取消"
            ) from exc
        except Exception:
            logger.exception(
                "[%s] task failed | %.1fs",
                label,
                time.perf_counter() - started,
            )
            raise

        messages = result.get("messages", [])
        final = messages[-1] if messages else None
        logger.info(
            "[%s] task done | %.1fs | messages=%d",
            label,
            time.perf_counter() - started,
            len(messages),
        )
        if final is None:
            raise RuntimeError("Agent 没有返回任何消息")

        # 撞到 max_tokens 时正文可能为空，这种结果不能当成成功返回。
        self._check_finish_reason(final)
        content = self._postprocess_final(str(final.content), task)
        if content != str(final.content):
            final = final.model_copy(update={"content": content})
        if session_id:
            self.context.add_assistant(session_id, self._memory_content(content))

        return final

    async def astream(
        self,
        task: str,
        *,
        recursion_limit: int = 20,
        thread_id: str | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        """流式执行任务。

        产出事件：

        - ``{"type": "progress", "text": "..."}``：工具调用准备阶段的状态提示；
        - ``{"type": "delta", "text": "..."}``：模型增量文本；
        - ``{"type": "done", "content": "..."}``：最终完整回答。

        ``values`` 模式用于拿最终 state，避免把中间 tool-call 文本当成结果。
        """
        if not task or not task.strip():
            raise ValueError("task 不能为空")

        messages, session_id = await self._build_context(
            task, user_id=user_id, thread_id=thread_id
        )
        label = type(self).__name__
        started = time.perf_counter()
        logger.info("[%s] stream start | %s", label, shorten(task, 160))

        config: dict[str, Any] = {
            "callbacks": [AgentLoopLogger(agent=label)],
            "recursion_limit": recursion_limit,
        }

        final_state: Any = None
        progress_sent = False
        try:
            async with asyncio.timeout(self.timeout):
                async for mode, payload in self._agent.astream(
                    {"messages": _to_langchain_messages(messages)},
                    config=config,
                    stream_mode=["messages", "values"],
                ):
                    if mode == "messages":
                        chunk, _metadata = payload
                        # messages 模式除了模型 token，还会推送 tool 节点的 ToolMessage
                        # 和 chain 结束时的完整消息；只转发模型 token，避免把工具原始
                        # 结果（如 read_file 的大段 JSON）当成回答流式打印出来。
                        if not isinstance(chunk, AIMessageChunk):
                            continue
                        tool_chunks = (
                            getattr(chunk, "tool_call_chunks", None)
                            or getattr(chunk, "tool_calls", None)
                            or []
                        )
                        if tool_chunks and not progress_sent:
                            progress_sent = True
                            yield {"type": "progress", "text": "正在准备工具调用…"}
                        text = chunk.content
                        if isinstance(text, str) and text:
                            yield {"type": "delta", "text": text}
                    elif mode == "values":
                        final_state = payload
                        # 模型节点结束，下一次工具调用准备阶段可以再次提示。
                        progress_sent = False
        except TimeoutError as exc:
            logger.error(
                "[%s] stream timeout | %.1fs | limit=%ss",
                label,
                time.perf_counter() - started,
                self.timeout,
            )
            raise AgentTimeoutError(
                f"{label} 执行超过 {self.timeout:g}s，已取消"
            ) from exc
        except Exception:
            logger.exception(
                "[%s] stream failed | %.1fs",
                label,
                time.perf_counter() - started,
            )
            raise

        messages = (final_state or {}).get("messages", [])
        final = messages[-1] if messages else None
        if final is None:
            raise RuntimeError("Agent 没有返回任何消息")

        self._check_finish_reason(final)
        content = self._postprocess_final(str(final.content), task)
        if session_id:
            self.context.add_assistant(session_id, self._memory_content(content))
        logger.info(
            "[%s] stream done | %.1fs | messages=%d",
            label,
            time.perf_counter() - started,
            len(messages),
        )
        yield {"type": "done", "content": content}


__all__ = [
    "AgentTimeoutError",
    "AgentType",
    "BaseAgent",
    "COMMON_RULES",
    "SKILL_FILES",
    "SKILLS_DIR",
]
