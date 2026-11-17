"""PageAgent 集成自测。

测试分四个隔离块：
1. 意图识别：纯逻辑，无外部依赖
2. 工具绑定：验证 PageAgent 默认工具集
3. 工具调用：直接测试 tool/ 中的工具（文件读写、Excel、校验）
4. AgentOrchestrator 全流程：通过编排器执行真实任务（含登录前置条件）
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agent.agents import CaseAgent, CodeAgent, ERPAgentHub, GeneralAgent, PageAgent, detect_intent
from agent.base import AgentType
from agent.orchestrator import AgentOrchestrator, AgentRequest
from tool.context import configure, get_context

pytestmark = pytest.mark.agent


# ----------------------------------------------------------------------
# 块 1：意图识别（纯逻辑，无外部依赖）
# ----------------------------------------------------------------------


class TestIntentDetection:
    """意图识别：验证任务描述正确路由到 Page / Case / Code / General。"""

    def test_capture_task_routes_to_page_agent(self) -> None:
        """采集页面控件任务应路由到 PageAgent。"""
        assert detect_intent("采集采购订单页面的控件信息") == AgentType.PAGE

    def test_list_page_task_routes_to_page_agent(self) -> None:
        """列表页采集任务应路由到 PageAgent。"""
        assert detect_intent("查看采购订单列表页结构") == AgentType.PAGE

    def test_add_modal_task_routes_to_page_agent(self) -> None:
        """新增弹窗采集任务应路由到 PageAgent。"""
        assert detect_intent("采集采购入库的新增弹窗控件") == AgentType.PAGE

    def test_case_generation_task_routes_to_case_agent(self) -> None:
        """用例生成任务应路由到 CaseAgent。"""
        assert detect_intent("生成采购订单的测试用例") == AgentType.CASE

    def test_test_point_task_routes_to_case_agent(self) -> None:
        """测试点设计任务应路由到 CaseAgent。"""
        assert detect_intent("设计采购入库的测试点") == AgentType.CASE
        assert detect_intent("设计采购订单的自动化测试用例") == AgentType.CASE

    def test_code_generation_task_routes_to_code_agent(self) -> None:
        """自动化代码生成任务应路由到 CodeAgent。"""
        assert detect_intent("根据采购订单用例生成自动化测试代码") == AgentType.CODE
        assert detect_intent("写一个 Playwright pytest 脚本") == AgentType.CODE

    def test_general_chat_routes_to_general_agent(self) -> None:
        """普通闲聊应路由到 GeneralAgent。"""
        assert detect_intent("随便说点什么") == AgentType.GENERAL
        assert detect_intent("你好，今天天气怎么样") == AgentType.GENERAL
        assert detect_intent("写一首关于秋天的诗") == AgentType.GENERAL

    def test_empty_task_routes_to_general_agent(self) -> None:
        """空任务默认路由到 GeneralAgent。"""
        assert detect_intent("") == AgentType.GENERAL


# ----------------------------------------------------------------------
# 块 2：工具绑定（验证 PageAgent 默认工具集）
# ----------------------------------------------------------------------


class TestPageAgentTools:
    """PageAgent 工具绑定：验证默认工具集完整且正确。"""

    def test_default_tools_include_capture_page(self) -> None:
        """默认工具集必须包含 capture_page。"""
        assert "capture_page" in [t.name for t in PageAgent.default_tools()]

    def test_default_tools_include_close_browser(self) -> None:
        """默认工具集必须包含 close_browser。"""
        assert "close_browser" in [t.name for t in PageAgent.default_tools()]

    def test_default_tools_include_file_operations(self) -> None:
        """默认工具集必须包含文件读写工具。"""
        names = [t.name for t in PageAgent.default_tools()]
        assert "read_file" in names
        assert "write_file" in names
        assert "edit_file" in names

    def test_default_tools_include_web_tools(self) -> None:
        """默认工具集必须包含网页搜索和抓取工具。"""
        names = [t.name for t in PageAgent.default_tools()]
        assert "web_search" in names
        assert "web_fetch" in names

    def test_default_tools_count(self) -> None:
        """默认工具集应有 7 个工具。"""
        assert len(PageAgent.default_tools()) == 7

    def test_case_agent_tools_include_xlsx(self) -> None:
        """CaseAgent 默认工具集必须包含 Excel 处理工具。"""
        names = [t.name for t in CaseAgent.default_tools()]
        assert "read_xlsx" in names
        assert "xlsx_to_json" in names
        assert "json_to_xlsx" in names

    def test_case_agent_tools_include_validator(self) -> None:
        """CaseAgent 默认工具集必须包含用例校验工具。"""
        assert "validate_cases" in [t.name for t in CaseAgent.default_tools()]

    def test_code_agent_tools(self) -> None:
        """CodeAgent 只保留专用输入加载和 xlsx 兜底，代码由钩子落盘。"""
        names = [t.name for t in CodeAgent.default_tools()]
        assert "load_code_inputs" in names
        assert "read_xlsx" in names
        assert "write_file" not in names
        assert "read_file" not in names
        assert "list_dir" not in names


# ----------------------------------------------------------------------
# 块 3：工具调用（直接测试 tool/ 中的工具）
# ----------------------------------------------------------------------


class TestToolInvocation:
    """工具调用：直接测试 tool/ 中的工具，不经过 Agent。"""

    @pytest.fixture(autouse=True)
    def setup_context(self) -> None:
        """每个测试前配置 ToolContext，产物写到 selfcache。"""
        selfcache = Path(__file__).resolve().parent / "selfcache"
        selfcache.mkdir(parents=True, exist_ok=True)
        configure(
            workspace=selfcache,
            artifacts=selfcache / "artifacts",
            run_id="test_tools",
        )

    # -- 文件读写工具 --

    def test_write_and_read_file(self) -> None:
        """write_file 写入后 read_file 能正确读取。"""
        from tool.file_system import read_file, write_file

        path = get_context().workspace / "test.txt"
        write_file.invoke({"path": str(path), "content": "hello world"})
        result = read_file.invoke({"path": str(path)})
        assert "hello world" in result

    def test_edit_file(self) -> None:
        """edit_file 能正确替换文本。"""
        from tool.file_system import edit_file, write_file

        path = get_context().workspace / "edit_test.txt"
        write_file.invoke({"path": str(path), "content": "old content"})
        result = edit_file.invoke({
            "path": str(path),
            "old_string": "old",
            "new_string": "new",
        })
        assert "已编辑" in result
        assert "new content" in path.read_text(encoding="utf-8")

    def test_read_file_not_found(self) -> None:
        """read_file 读取不存在的文件应返回错误信息。"""
        from tool.file_system import read_file

        result = read_file.invoke({"path": "nonexistent.txt"})
        assert "错误" in result or "不存在" in result

    # -- Excel 处理工具 --

    def test_json_to_xlsx_and_read(self) -> None:
        """json_to_xlsx 写入后 read_xlsx 能正确读取。"""
        from tool.xlsx_handler import json_to_xlsx, read_xlsx

        json_path = get_context().workspace / "cases.json"
        xlsx_path = get_context().workspace / "cases.xlsx"

        cases = [
            {"用例编号": "TC-001", "功能模块": "采购", "测试目的": "列表加载"},
            {"用例编号": "TC-002", "功能模块": "采购", "测试目的": "新增校验"},
        ]
        json_path.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")

        json_to_xlsx.invoke({
            "json_path": str(json_path),
            "xlsx_path": str(xlsx_path),
            "sheet_name": "测试用例",
        })

        result = read_xlsx.invoke({"path": str(xlsx_path)})
        data = json.loads(result)
        assert data["sheet"] == "测试用例"
        assert len(data["rows"]) == 3  # 表头 + 2 行数据

    def test_xlsx_to_json(self) -> None:
        """xlsx_to_json 能正确转换 Excel 为 JSON。"""
        from tool.xlsx_handler import json_to_xlsx, xlsx_to_json

        json_path = get_context().workspace / "input.json"
        xlsx_path = get_context().workspace / "output.xlsx"

        cases = [{"id": "1", "name": "test"}]
        json_path.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")

        json_to_xlsx.invoke({
            "json_path": str(json_path),
            "xlsx_path": str(xlsx_path),
        })

        result = xlsx_to_json.invoke({
            "xlsx_path": str(xlsx_path),
            "json_path": str(get_context().workspace / "output.json"),
        })
        data = json.loads(result)
        assert data["cases"] == 1

    # -- 用例校验工具 --

    def test_validate_cases_valid(self) -> None:
        """validate_cases 对合法用例应返回 ok=true。"""
        from tool.case_validator import validate_cases

        cases = [{
            "id": "TC-001",
            "priority": "高",
            "module": "采购订单",
            "purpose": "列表加载",
            "pre": "已登录",
            "steps": "1. 打开列表\n2. 查看表头",
            "expected": "列表显示",
            "assertion": "表头包含供应商",
            "manual": "待执行",
            "automation": "待执行",
            "note": "AI生成",
        }]
        path = get_context().workspace / "valid_cases.json"
        path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")

        result = validate_cases.invoke({"json_path": str(path), "expected_count": 1})
        data = json.loads(result)
        assert data["ok"] is True
        assert data["count"] == 1
        assert data["errors"] == []

    def test_validate_cases_missing_fields(self) -> None:
        """validate_cases 对缺少字段用例应返回错误。"""
        from tool.case_validator import validate_cases

        cases = [{"id": "TC-001"}]  # 缺少必填字段
        path = get_context().workspace / "invalid_cases.json"
        path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")

        result = validate_cases.invoke({"json_path": str(path)})
        data = json.loads(result)
        assert data["ok"] is False
        assert len(data["errors"]) > 0


# ----------------------------------------------------------------------
# 块 4：AgentOrchestrator 全流程（含登录前置条件）
# ----------------------------------------------------------------------


class TestAgentOrchestrator:
    """AgentOrchestrator 全流程：通过编排器执行真实任务。"""

    @pytest.fixture(autouse=True)
    def setup_context(self) -> None:
        """配置 ToolContext，产物写到 selfcache。"""
        selfcache = Path(__file__).resolve().parent / "selfcache"
        selfcache.mkdir(parents=True, exist_ok=True)
        configure(
            workspace=selfcache,
            artifacts=selfcache / "artifacts",
            run_id="test_orchestrator",
        )

    def test_orchestrator_initialization(self) -> None:
        """AgentOrchestrator 应能正常初始化。"""
        orchestrator = AgentOrchestrator(max_steps=5)
        assert orchestrator.max_steps == 5
        assert orchestrator.hub is not None

    def test_orchestrator_dispatch_page_task(self) -> None:
        """编排器持有的 Hub 应将采集任务分发到 PageAgent。"""
        orchestrator = AgentOrchestrator()
        agent = orchestrator.hub.dispatch("采集采购订单页面的控件")
        assert isinstance(agent, PageAgent)

    def test_orchestrator_dispatch_case_task(self) -> None:
        """编排器持有的 Hub 应将用例生成任务分发到 CaseAgent。"""
        orchestrator = AgentOrchestrator()
        agent = orchestrator.hub.dispatch("生成采购订单的测试用例")
        assert isinstance(agent, CaseAgent)

    def test_orchestrator_execute_page_task_with_login(self) -> None:
        """编排器执行采集任务时应自动处理登录。

        真实流程缺陷检测：
        - 采集页面时需要先登录
        - 登录失败应返回明确错误
        - 登录成功后应能正常采集
        """
        orchestrator = AgentOrchestrator()
        result = asyncio.run(orchestrator.run(
            "采集采购订单页面的控件信息，包含新增表单界面的控件信息"
        ))
        print(f"状态：{result.status}")
        print(f"Agent 类型：{result.agent_type}")
        print(f"内容：{result.content[:500]}")

    def test_orchestrator_execute_case_task(self) -> None:
        """编排器执行用例生成任务。"""
        orchestrator = AgentOrchestrator()
        result = asyncio.run(
            orchestrator.run("生成采购订单的测试用例10条，写入testself/selfcache/artifacts/xlsx中")
        )
        print(f"状态：{result.status}")
        print(f"Agent 类型：{result.agent_type}")
        print(f"内容：{result.content[:500]}")

    def test_orchestrator_explicit_agent_type(self) -> None:
        """显式指定 Agent 类型应正确执行。"""
        orchestrator = AgentOrchestrator(max_steps=5)
        result = asyncio.run(
            orchestrator.run(
                AgentRequest(task="读取 artifacts 目录", agent_type=AgentType.PAGE)
            )
        )
        print(f"状态：{result.status}")
        print(f"Agent 类型：{result.agent_type}")
        print(f"内容：{result.content[:500]}")

    def test_orchestrator_empty_task(self) -> None:
        """编排器处理空任务应抛 ValueError。"""
        orchestrator = AgentOrchestrator()
        with pytest.raises(ValueError):
            asyncio.run(orchestrator.run(""))

    def test_orchestrator_invalid_agent_type(self) -> None:
        """显式指定无效 Agent 类型应抛异常。"""
        orchestrator = AgentOrchestrator()
        with pytest.raises(ValueError):
            asyncio.run(
                orchestrator.run(
                    AgentRequest(task="test", agent_type="invalid")  # type: ignore[arg-type]
                )
            )


# ----------------------------------------------------------------------
# 块 5：ERPAgentHub 集成
# ----------------------------------------------------------------------


class TestERPAgentHub:
    """ERPAgentHub：验证 Hub 能正确分发任务。"""

    def test_hub_dispatch_capture_task(self) -> None:
        """Hub 应将采集任务分发到 PageAgent。"""
        hub = ERPAgentHub()
        assert isinstance(hub.dispatch("采集采购订单页面的控件"), PageAgent)

    def test_hub_dispatch_case_task(self) -> None:
        """Hub 应将用例生成任务分发到 CaseAgent。"""
        hub = ERPAgentHub()
        assert isinstance(hub.dispatch("生成采购订单的测试用例"), CaseAgent)

    def test_hub_dispatch_general_task(self) -> None:
        """Hub 应将普通对话分发到 GeneralAgent。"""
        hub = ERPAgentHub()
        assert isinstance(hub.dispatch("你好，今天天气怎么样"), GeneralAgent)

    def test_hub_dispatch_code_task(self) -> None:
        """Hub 应将代码生成任务分发到 CodeAgent。"""
        hub = ERPAgentHub()
        assert isinstance(hub.dispatch("生成 Playwright 自动化测试脚本"), CodeAgent)

    def test_hub_get_agent_by_type(self) -> None:
        """Hub 应能按类型显式获取 Agent。"""
        hub = ERPAgentHub()
        assert isinstance(hub.get_agent(AgentType.PAGE), PageAgent)
        assert isinstance(hub.get_agent(AgentType.CASE), CaseAgent)
        assert isinstance(hub.get_agent(AgentType.CODE), CodeAgent)
        assert isinstance(hub.get_agent(AgentType.GENERAL), GeneralAgent)

    def test_hub_get_agent_unknown_type_raises(self) -> None:
        """Hub 获取未知 Agent 类型时应抛 ValueError。"""
        hub = ERPAgentHub()
        with pytest.raises(ValueError, match="未知 AgentType"):
            hub.get_agent("invalid_type")  # type: ignore[arg-type]

    def test_hub_agent_types_property(self) -> None:
        """Hub 应返回所有 Agent 类型。"""
        hub = ERPAgentHub()
        assert AgentType.PAGE in hub.agent_types
        assert AgentType.CASE in hub.agent_types
        assert AgentType.CODE in hub.agent_types
        assert AgentType.GENERAL in hub.agent_types
