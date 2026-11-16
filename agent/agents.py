"""四个 Agent 及统一调度入口。

按职责划分：

- ``PageAgent``：采集控件、执行 UI 操作、调用 HTTP、读写文件；
- ``CaseAgent``：读取控件结果、生成用例、校验并写 xlsx；
- ``CodeAgent``：根据用例生成 pytest + Playwright 自动化代码；
- ``GeneralAgent``：处理与 ERP 测试无关的普通对话。

意图识别优先匹配代码生成，其次用例生成，再次页面/测试操作，其余交给通用对话。
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
from context import ContextBuilder


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
        context_builder: ContextBuilder | None = None,
    ) -> None:
        default = tools if tools is not None else self.default_tools()
        super().__init__(
            llm=llm,
            tools=default,
            context_builder=context_builder,
        )

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

    agent_type = AgentType.CASE
    system_prompt = (
        "你是 ERP 测试用例生成 Agent，负责检索业务知识、读取控件采集结果，"
        "生成覆盖正常、边界和异常场景的结构化用例，校验通过后写入 xlsx。"
        "你不执行用例、不修改业务数据。"
        "只查 artifacts/dom：路径不明确时先用 list_dir 查看 artifacts/dom，"
        "再读取 <module>_capture.json。"
        "如果目标模块的 <module>_capture.json 不存在，立即停止，不再调用任何工具，"
        "直接回复：未找到 <module> 的控件采集结果，请先采集后再生成用例。"
        "禁止读取其他模块的控件，禁止遍历 docs/learning/frontend 等目录。"
    )

    def __init__(
        self,
        llm: BaseChatModel,
        tools: Sequence[BaseTool] | None = None,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        default = tools if tools is not None else self.default_tools()
        super().__init__(
            llm=llm,
            tools=default,
            context_builder=context_builder,
        )

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


class CodeAgent(BaseAgent):
    """自动化测试代码生成：用例 -> pytest + Playwright 代码。"""

    agent_type = AgentType.CODE
    system_prompt = (
        "你是 ERP 自动化测试代码生成 Agent，负责读取控件和用例，"
        "生成可运行的 pytest + Playwright 测试代码。"
        "你不执行生成的代码，不修改业务数据，只写代码文件并说明路径。"
        "读取输入按优先级：artifacts/controls/<module>.json、"
        "artifacts/dom/<module>_facts_compact.json；"
        "禁止整读 <module>_capture.json，需要时只能用 read_file 的 offset/limit 读局部；"
        "不要浏览 examples/core/tool/agent/utils 等与目标模块无关的目录；"
        "拿到控件和用例后立即生成代码。"
    )

    def __init__(
        self,
        llm: BaseChatModel,
        tools: Sequence[BaseTool] | None = None,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        default = tools if tools is not None else self.default_tools()
        super().__init__(
            llm=llm,
            tools=default,
            context_builder=context_builder,
        )

    @staticmethod
    def default_tools() -> list[BaseTool]:
        """文件读写 + 目录 + xlsx 读取。"""
        from tool.file_system import edit_file, list_dir, read_file, write_file
        from tool.xlsx_handler import read_xlsx
        return [read_file, write_file, edit_file, list_dir, read_xlsx]


class GeneralAgent(BaseAgent):
    """通用对话：处理与 ERP 测试无关的问题。"""

    agent_type = AgentType.GENERAL
    system_prompt = (
        "你是通用对话助手，负责回答与 ERP 测试无关的问题。"
        "直接、简洁地回答；不调用工具，不编造 ERP 数据。"
        "如果用户实际想做 ERP 测试，请提示其重新描述为采集控件或生成测试用例任务。"
    )

    def __init__(
        self,
        llm: BaseChatModel,
        tools: Sequence[BaseTool] | None = None,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        default = tools if tools is not None else self.default_tools()
        super().__init__(
            llm=llm,
            tools=default,
            context_builder=context_builder,
        )

    @staticmethod
    def default_tools() -> list[BaseTool]:
        """通用对话不使用测试工具。"""
        return []


# 意图识别：代码生成优先，其次用例、页面/测试操作，其余交给通用对话。
CODE_KEYWORDS = (
    "代码", "脚本", "pytest", "playwright", "生成代码",
)
PAGE_KEYWORDS = (
    "采集", "控件", "页面", "列表", "新增", "弹窗", "按钮", "表单",
    "登录", "截图", "菜单", "点击", "填写", "提交", "查询", "浏览器",
    "ERP", "采购", "销售", "零售", "库存", "订单", "供应商", "商品",
    "接口", "HTTP", "UI",
)
INTENT_RULES: tuple[tuple[AgentType, tuple[str, ...]], ...] = (
    (AgentType.CODE, CODE_KEYWORDS),
    (AgentType.CASE, ("用例", "测试点", "测试场景", "测试设计", "生成测试")),
    (AgentType.PAGE, PAGE_KEYWORDS),
)
DEFAULT_AGENT_TYPE = AgentType.GENERAL


def _contains(text: str, keyword: str) -> bool:
    """中文按子串匹配，英文按单词边界匹配。"""
    if keyword.isascii():
        return re.search(rf"\b{re.escape(keyword.lower())}\b", text) is not None
    return keyword in text


def detect_intent(task: str) -> AgentType:
    """根据任务描述识别应该由哪类 Agent 执行。"""
    text = (task or "").lower()
    for agent_type, keywords in INTENT_RULES:
        if any(_contains(text, keyword) for keyword in keywords):
            return agent_type
    return DEFAULT_AGENT_TYPE


class ERPAgentHub:
    """四个 Agent 的统一入口，供编排器或调用方持有和调用。

    - ``dispatch(task)``：只做意图识别，返回选中的 Agent；
    - ``run(task)``：识别意图并执行，返回 ``AIMessage``；
    - ``get_agent(agent_type)``：按类型显式取 Agent，跳过意图识别。
    """

    def __init__(
        self,
        *,
        fast_llm: BaseChatModel | None = None,
        strong_llm: BaseChatModel | None = None,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        fast = fast_llm or build_llm()
        # 只注入 fast_llm 时复用它，方便测试；都没传才按配置创建强模型。
        strong = strong_llm or (fast_llm if fast_llm is not None else build_llm(strong=True))

        # 四个 Agent 共用一个 ContextBuilder：同一 thread 的工作记忆、画像和
        # 情景记忆保持一致，路由切换 Agent 时也能接上历史。
        self.context = context_builder or ContextBuilder()
        self.agents: dict[AgentType, BaseAgent] = {
            AgentType.PAGE: PageAgent(fast, context_builder=self.context),
            AgentType.CASE: CaseAgent(strong, context_builder=self.context),
            AgentType.CODE: CodeAgent(strong, context_builder=self.context),
            AgentType.GENERAL: GeneralAgent(fast, context_builder=self.context),
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

    async def run(
        self,
        task: str,
        *,
        agent_type: AgentType | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
    ) -> AIMessage:
        """识别意图并执行任务。"""
        return await self.dispatch(task, agent_type=agent_type).run(
            task, thread_id=thread_id, user_id=user_id
        )


__all__ = [
    "CaseAgent",
    "CodeAgent",
    "CODE_KEYWORDS",
    "DEFAULT_AGENT_TYPE",
    "ERPAgentHub",
    "GeneralAgent",
    "INTENT_RULES",
    "PAGE_KEYWORDS",
    "PageAgent",
    "detect_intent",
]
