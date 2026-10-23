"""ERP 测试用例领域模型。

只定义数据结构，不负责 Excel 列解析、文本拆分或状态归一化。
这些转换统一放在 ``agent.parser.extractor`` 中。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class Priority(str, Enum):
    """测试用例优先级，与 Excel 模板取值一致。"""

    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class InputSource(str, Enum):
    """测试用例来源。"""

    EXCEL = "excel"
    JIRA = "jira"
    ADO = "ado"
    TEXT = "text"


class TestCase(BaseModel):
    """一条测试用例，字段与 Excel 的 12 列模板对应。"""

    id: str = Field(description="用例编号，如 采购-001")
    priority: Priority = Field(default=Priority.MEDIUM)
    module: str = ""
    purpose: str = ""
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected_result: str = ""
    assertions: str = ""
    manual_status: str = "待执行"
    automation_status: str = "待生成"
    tester: str = ""
    ai_mark: str = ""


class TestSpec(BaseModel):
    """Stage 1 的归一化结果，后续阶段只消费该模型。"""

    source: InputSource = InputSource.EXCEL
    source_id: str = Field(description="Excel 路径、sheet 名或 ticket ID")
    title: str
    sheet: str | None = None
    module: str | None = None
    test_cases: list[TestCase] = Field(default_factory=list)
    raw_text: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
