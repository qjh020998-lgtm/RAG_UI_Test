"""Agent 基类：固定类型与提示词，模型和工具由具体 Agent 传入。

ReAct 循环交给 LangGraph ``create_react_agent``，本模块不实现工具执行、重试或轮数控制。
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from enum import Enum
from functools import lru_cache
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent

from agent.loop_logger import AgentLoopLogger, shorten
from utils.logger import get_logger

logger = get_logger(__name__)


class AgentType(str, Enum):
    """ERP 测试平台的两类 Agent。"""

    PAGE = "page"                              # 页面交互：采集控件、UI 操作、HTTP 调用
    CASE_GENERATION = "case_generation"        # 用例生成：知识检索 + 用例生成 + xlsx


SKILLS_DIR = Path(__file__).resolve().parents[1] / "Skills"

# 每类 Agent 的技能文件（相对 Skills/ 目录），拼进 system prompt。
SKILL_FILES: dict[AgentType, str] = {
    AgentType.PAGE: "page/SKILL.md",
    AgentType.CASE_GENERATION: "case_generation/SKILL.md",
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

    def __init__(self, llm: BaseChatModel, tools: Sequence[BaseTool] | None = None) -> None:
        if not isinstance(getattr(self, "agent_type", None), AgentType):
            raise TypeError(f"{type(self).__name__} 必须定义 agent_type: AgentType")
        if llm is None:
            raise ValueError("llm 不能为空")

        self.llm = llm
        self.tools: list[BaseTool] = list(tools or [])
        self._agent = create_react_agent(
            self.llm,
            tools=self.tools,
            prompt=self.build_system_prompt(),
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

    async def run(self, task: str) -> AIMessage:
        """执行一次任务，返回最终 AIMessage。"""
        if not task or not task.strip():
            raise ValueError("task 不能为空")

        label = type(self).__name__
        started = time.perf_counter()
        logger.info("[%s] task start | %s", label, shorten(task, 160))

        try:
            result = await self._agent.ainvoke(
                {"messages": [HumanMessage(content=task)]},
                config={"callbacks": [AgentLoopLogger(agent=label)]},
            )
        except Exception:
            logger.exception(
                "[%s] task failed | %.1fs",
                label,
                time.perf_counter() - started,
            )
            raise

        logger.info(
            "[%s] task done | %.1fs | messages=%d",
            label,
            time.perf_counter() - started,
            len(result.get("messages", [])),
        )
        return result["messages"][-1]


__all__ = [
    "AgentType",
    "BaseAgent",
    "COMMON_RULES",
    "SKILL_FILES",
    "SKILLS_DIR",
]
