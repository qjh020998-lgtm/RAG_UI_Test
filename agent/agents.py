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
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from agent.base import AgentType, BaseAgent
from agent.llm import build_llm
from context import ContextBuilder
from tool.context import get_context
from utils.logger import get_logger

logger = get_logger(__name__)


_CODE_FILE_BLOCK = re.compile(
    r"```(?:python)?[ \t]+file=([^\s`]+)[ \t]*\r?\n(.*?)```",
    re.DOTALL,
)


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
        "你不执行生成的代码，不修改业务数据。"
        "读取输入只调用 load_code_inputs(module)：它按当前运行配置的 artifacts 根目录"
        "读取整理后的控件和用例，开发/测试环境自动隔离。"
        "不要调用 list_dir/read_file，不要读取 .env、pyproject.toml、pytest.ini、"
        "conftest.py、config.yaml、Skills、testself 等项目文件。"
        "不要调用 write_file。把完整测试代码放在一个带 file= 的 Python 代码块中返回，"
        "例如 ```python file=tests/generated/test_采购订单.py ...```，系统会自动落盘。"
        "如果 load_code_inputs 报错，直接说明缺哪个文件，不要猜测或用其他模块替代。"
    )

    def __init__(
        self,
        llm: BaseChatModel,
        tools: Sequence[BaseTool] | None = None,
        context_builder: ContextBuilder | None = None,
        timeout: float | None = None,
    ) -> None:
        default = tools if tools is not None else self.default_tools()
        super().__init__(
            llm=llm,
            tools=default,
            context_builder=context_builder,
            timeout=timeout,
        )
        self._last_output: Path | None = None

    @staticmethod
    def default_tools() -> list[BaseTool]:
        """专用输入加载 + xlsx 兜底；代码由 BaseAgent 钩子确定性落盘。"""
        from tool.file_system import load_code_inputs
        from tool.xlsx_handler import read_xlsx
        return [load_code_inputs, read_xlsx]

    def _postprocess_final(self, content: str, task: str) -> str:
        """把回答里带 file= 的代码块写入 tests/generated。"""
        self._last_output = None
        match = _CODE_FILE_BLOCK.search(content)
        if match is None:
            logger.warning("[CodeAgent] 回答中没有 file= 代码块，未落盘")
            return content + "\n\n> 未找到可落盘的代码块（需要 ```python file=...）"

        relative = match.group(1).strip().strip("\"'")
        code = match.group(2).rstrip() + "\n"
        workspace = get_context().workspace
        target = (workspace / relative).resolve()
        output_root = (workspace / "tests" / "generated").resolve()
        if target.suffix != ".py" or not target.is_relative_to(output_root):
            logger.warning("[CodeAgent] 输出路径不合法：%s", relative)
            return content + "\n\n> 输出路径必须位于 tests/generated/ 且为 .py 文件，未落盘。"

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code, encoding="utf-8")
        except OSError as exc:
            logger.exception("[CodeAgent] 代码落盘失败：%s", target)
            return content + f"\n\n> 代码落盘失败：{exc}"

        self._last_output = target
        logger.info("[CodeAgent] code written | %s | chars=%d", target, len(code))
        try:
            written = target.relative_to(workspace).as_posix()
        except ValueError:
            written = str(target)
        return f"{content.rstrip()}\n\n已写入：{written}"

    def _memory_content(self, content: str) -> str:
        """工作记忆只存摘要，避免整份代码撑爆下一轮上下文。"""
        if self._last_output is not None:
            try:
                relative = self._last_output.relative_to(get_context().workspace)
            except ValueError:
                relative = self._last_output
            return f"已生成测试代码：{relative.as_posix()}（完整代码见回答）"
        return content if len(content) <= 2000 else content[:2000] + "…"


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
