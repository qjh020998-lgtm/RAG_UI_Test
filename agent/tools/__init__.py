"""Agent 工具包。

每个工具提供：

- ``TOOL_SPEC``：给编排 Agent 看的 JSON Schema（名称、描述、参数）
- 一个可调用函数：输入参数，返回结构化结果

``TOOL_REGISTRY`` 把两者注册在一起，编排器按 name 查找即可。
"""
from agent.tools.page_element_search import (
    TOOL_SPEC as PAGE_ELEMENT_SEARCH_SPEC,
    search_page_elements,
)
from agent.tools.excel_utils import (
    XLSX_DEFAULT,
    REQUIRED_COLUMNS,
    load_pending_cases,
    parse_steps,
)
from agent.tools.page_utils import (
    append_code,
    covered,
    extract_step_descriptions,
    indent_code,
    load_control_cache,
    strip_fence,
)
from agent.tools.step_registry import (
    TOOL_SPEC as STEP_REGISTRY_SPEC,
    build_registry,
    diff_registry,
    refresh_registry,
    write_registry,
)

TOOL_REGISTRY: dict[str, dict] = {
    PAGE_ELEMENT_SEARCH_SPEC["name"]: {
        "spec": PAGE_ELEMENT_SEARCH_SPEC,
        "func": search_page_elements,
    },
    STEP_REGISTRY_SPEC["name"]: {
        "spec": STEP_REGISTRY_SPEC,
        "func": refresh_registry,
    },
}

__all__ = [
    "TOOL_REGISTRY",
    "PAGE_ELEMENT_SEARCH_SPEC",
    "search_page_elements",
    "STEP_REGISTRY_SPEC",
    "build_registry",
    "diff_registry",
    "refresh_registry",
    "write_registry",
    "XLSX_DEFAULT",
    "REQUIRED_COLUMNS",
    "load_pending_cases",
    "parse_steps",
    "append_code",
    "covered",
    "extract_step_descriptions",
    "indent_code",
    "load_control_cache",
    "strip_fence",
]
