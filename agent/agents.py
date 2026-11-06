"""两个 ERP 测试 Agent 及统一调度入口。

按「外部交互」与「数据处理」划分：

- ``PageAgent``：采集控件、执行 UI 操作、调用 HTTP、读写文件；
- ``CaseAgent``：检索知识、读取控件结果、生成用例、校验并写 xlsx。

意图识别只判断是否属于用例生成，其余任务都交给 PageAgent。
"""
from __future__ import annotations

import re
import sys
from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from agent.base import AgentType, BaseAgent
from agent.llm import build_llm


class PageAgent(BaseAgent):
    """页面交互：采集控件、执行 UI 操作、调用 HTTP、读写文件。"""

    agent_type = AgentType.PAGE
    system_prompt = (
        "你是 ERP 页面交互 Agent，负责与外部系统交互：用 Playwright 采集页面控件、"
        "执行 UI 操作，用 web_fetch 调用 HTTP 接口，用搜索/抓取补充语义，并把结果写成文件。"
        "你不生成测试用例，只负责采集、交互和证据收集。"
    )

    def __init__(
        self,
        llm: BaseChatModel,
        tools: Sequence[BaseTool] | None = None,
    ) -> None:
        default = tools if tools is not None else self.default_tools()
        super().__init__(llm=llm, tools=default)

    @staticmethod
    def default_tools() -> list[BaseTool]:
        """浏览器采集 + 搜索抓取 + 文件读写。"""
        from tool.file_system import edit_file, read_file, write_file
        from tool.web import capture_page, close_browser, web_fetch, web_search
        return [
            capture_page, close_browser,
            web_fetch, web_search,
            read_file, write_file, edit_file,
        ]


class CaseAgent(BaseAgent):
    """用例生成：知识检索 + 控件结果 -> 结构化用例 -> 校验 -> xlsx。
    补充：需要加上知识检索和控件采集结果压缩以及结构校验
    """

    agent_type = AgentType.CASE_GENERATION
    system_prompt = (
        "你是 ERP 测试用例生成 Agent，负责检索业务知识、读取控件采集结果，"
        "生成覆盖正常、边界和异常场景的结构化用例，校验通过后写入 xlsx。"
        "你不执行用例、不修改业务数据。"
        "路径不明确时先用 list_dir 查看 artifacts/dom，再读取 <module>_capture.json。"
    )

    def __init__(
        self,
        llm: BaseChatModel,
        tools: Sequence[BaseTool] | None = None,
    ) -> None:
        default = tools if tools is not None else self.default_tools()
        super().__init__(llm=llm, tools=default)

    @staticmethod
    def default_tools() -> list[BaseTool]:
        """文件读写 + 目录列举 + Excel 处理 + 用例校验 + 搜索。"""
        from tool.case_validator import validate_cases
        from tool.file_system import edit_file, list_dir, read_file, write_file
        from tool.web import web_search
        from tool.xlsx_handler import json_to_xlsx, read_xlsx, xlsx_to_json
        return [
            read_file, write_file, edit_file, list_dir,
            read_xlsx, xlsx_to_json, json_to_xlsx,
            validate_cases, web_search,
        ]


# 意图识别：命中用例相关关键词走 CaseAgent，其余全部走 PageAgent。
INTENT_RULES: tuple[tuple[AgentType, tuple[str, ...]], ...] = (
    (AgentType.CASE_GENERATION, ("用例", "测试点", "测试场景", "测试设计", "生成测试")),
)
DEFAULT_AGENT_TYPE = AgentType.PAGE


def _contains(text: str, keyword: str) -> bool:
    """中文按子串匹配，英文按单词边界匹配。"""
    if keyword.isascii():
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword in text


def detect_intent(task: str) -> AgentType:
    """根据任务描述识别应该由哪类 Agent 执行。"""
    text = (task or "").lower()
    for agent_type, keywords in INTENT_RULES:
        if any(_contains(text, keyword) for keyword in keywords):
            return agent_type
    return DEFAULT_AGENT_TYPE


class ERPAgentHub:
    """两个 Agent 的统一入口，供 AgentRunner 持有和调用。

    - ``dispatch(task)``：只做意图识别，返回选中的 Agent；
    - ``run(task)``：识别意图并执行，返回 ``AIMessage``；
    - ``get_agent(agent_type)``：按类型显式取 Agent，跳过意图识别。
    """

    def __init__(
        self,
        *,
        fast_llm: BaseChatModel | None = None,
        strong_llm: BaseChatModel | None = None,
    ) -> None:
        fast = fast_llm or build_llm()
        # 只注入 fast_llm 时复用它，方便测试；都没传才按配置创建强模型。
        strong = strong_llm or (fast_llm if fast_llm is not None else build_llm(strong=True))

        self.agents: dict[AgentType, BaseAgent] = {
            AgentType.PAGE: PageAgent(fast),
            AgentType.CASE_GENERATION: CaseAgent(strong),
        }

    @property
    def agent_types(self) -> tuple[AgentType, ...]:
        return tuple(self.agents)

    def get_agent(self, agent_type: AgentType) -> BaseAgent:
        """按类型取 Agent；类型不存在时抛 ValueError。"""
        try:
            return self.agents[agent_type]
        except KeyError as exc:
            raise ValueError(f"未知 AgentType：{agent_type}") from exc

    def dispatch(self, task: str, *, agent_type: AgentType | None = None) -> BaseAgent:
        """识别任务意图并返回对应 Agent；显式传 agent_type 时跳过识别。"""
        if not task or not task.strip():
            raise ValueError("task 不能为空")
        return self.get_agent(agent_type or detect_intent(task))

    async def run(self, task: str, *, agent_type: AgentType | None = None) -> AIMessage:
        """识别意图并执行任务。"""
        return await self.dispatch(task, agent_type=agent_type).run(task)


__all__ = [
    "CaseAgent",
    "DEFAULT_AGENT_TYPE",
    "ERPAgentHub",
    "INTENT_RULES",
    "PageAgent",
    "detect_intent",
]
