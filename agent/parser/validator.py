"""TestCase / TestSpec / Excel 工作簿校验。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.models import TestCase, TestSpec
from agent.parser.excel_reader import read_workbook


MANUAL_STATUSES = {"", "通过", "失败", "跳过", "待执行", "待补充"}
AUTOMATION_STATUSES = {"", "通过", "失败", "跳过", "待生成", "待补充"}


@dataclass(frozen=True)
class ValidationIssue:
    case_id: str | None
    field: str
    message: str
    sheet: str | None = None

    def __str__(self) -> str:
        location = f"{self.sheet or '-'} / {self.case_id or '-'}"
        return f"{location} [{self.field}] {self.message}"


class ValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("\n".join(str(issue) for issue in issues))


def _validate_case(case: TestCase) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    def add(field: str, message: str) -> None:
        issues.append(ValidationIssue(case_id=case.id, field=field, message=message))

    if not case.id.strip():
        add("id", "用例编号为空")
    if not case.module.strip():
        add("module", "功能模块为空")
    if not case.purpose.strip():
        add("purpose", "测试目的为空")
    if not case.steps:
        add("steps", "测试步骤为空")
    if not case.expected_result.strip():
        add("expected_result", "预期结果为空")
    if not case.assertions.strip():
        add("assertions", "断言校验为空")
    if case.manual_status not in MANUAL_STATUSES:
        add("manual_status", f"非法手动状态: {case.manual_status}")
    if case.automation_status not in AUTOMATION_STATUSES:
        add("automation_status", f"非法自动化状态: {case.automation_status}")
    return issues


def validate_spec(spec: TestSpec) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for case in spec.test_cases:
        issues.extend(_validate_case(case))
        if case.id in seen:
            issues.append(
                ValidationIssue(
                    case_id=case.id,
                    field="id",
                    message="用例编号重复",
                    sheet=spec.sheet,
                )
            )
        seen.add(case.id)
    return issues


def validate_workbook(path: str | Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for sheet, spec in read_workbook(path).items():
        for issue in validate_spec(spec):
            issues.append(
                ValidationIssue(
                    case_id=issue.case_id,
                    field=issue.field,
                    message=issue.message,
                    sheet=sheet,
                )
            )
        for case in spec.test_cases:
            if case.id in seen:
                issues.append(
                    ValidationIssue(
                        case_id=case.id,
                        field="id",
                        message="跨 sheet 用例编号重复",
                        sheet=sheet,
                    )
                )
            seen.add(case.id)
    return issues


def assert_valid(spec: TestSpec) -> None:
    issues = validate_spec(spec)
    if issues:
        raise ValidationError(issues)


__all__ = [
    "ValidationIssue",
    "ValidationError",
    "validate_spec",
    "validate_workbook",
    "assert_valid",
]
