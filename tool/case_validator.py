"""测试用例 JSON 校验工具（validate_cases）。

不生成用例，只在写 xlsx 前检查结构：必填字段、编号唯一、模块一致、
数量、测试步骤 1./2./3. 连续编号、备注“AI生成”、执行情况“待执行”。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain.tools import tool
from pydantic import BaseModel, Field

from tool.context import get_context

_FIELDS = {
    "id": ("id", "用例编号"),
    "priority": ("priority", "优先级"),
    "module": ("module", "功能模块"),
    "purpose": ("purpose", "测试目的"),
    "pre": ("pre", "前置条件"),
    "steps": ("steps", "测试步骤"),
    "expected": ("expected", "预期结果"),
    "assertion": ("assertion", "断言校验"),
}


def _value(row: dict[str, Any], field: str) -> Any:
    for key in _FIELDS[field]:
        value = row.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _is_case_row(row: dict[str, Any]) -> bool:
    keys = set(row)
    return {"id", "module", "purpose"} <= keys or {"用例编号", "功能模块", "测试目的"} <= keys


def _steps_error(steps: Any) -> str | None:
    """校验步骤是否为 1. 2. 3. 的连续编号格式。"""
    lines = [line.strip() for line in str(steps).splitlines() if line.strip()]
    if not lines:
        return "测试步骤为空"
    for expected, line in enumerate(lines, start=1):
        match = re.fullmatch(r"(\d+)\.\s+(.+)", line)
        if not match:
            return f"测试步骤第 {expected} 行必须以 '{expected}. ' 开头"
        if int(match.group(1)) != expected:
            return f"测试步骤编号应从 1 连续递增，第 {expected} 行是 {match.group(1)}"
    return None


def validate_case_rows(
    rows: list[dict[str, Any]],
    *,
    expected_count: int = 0,
) -> list[str]:
    """返回错误列表；空列表表示通过。"""
    errors: list[str] = []
    if not rows:
        return ["用例列表为空"]
    if expected_count and len(rows) != expected_count:
        errors.append(f"用例数量应为 {expected_count}，实际 {len(rows)}")

    seen_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or not _is_case_row(row):
            errors.append(f"第 {index} 条不是测试用例对象")
            continue

        case_id = _value(row, "id")
        if case_id is None:
            errors.append(f"第 {index} 条缺少用例编号")
        else:
            case_id = str(case_id).strip()
            if case_id in seen_ids:
                errors.append(f"第 {index} 条用例编号重复：{case_id}")
            seen_ids.add(case_id)

        if _value(row, "module") is None:
            errors.append(f"第 {index} 条缺少功能模块")

        for field, label in (
            ("priority", "优先级"),
            ("purpose", "测试目的"),
            ("pre", "前置条件"),
            ("steps", "测试步骤"),
            ("expected", "预期结果"),
            ("assertion", "断言校验"),
        ):
            if _value(row, field) is None:
                errors.append(f"第 {index} 条缺少{label}")

        steps = _value(row, "steps")
        if steps is not None:
            step_error = _steps_error(steps)
            if step_error:
                errors.append(f"第 {index} 条{step_error}")

        note = row.get("note", row.get("备注"))
        if str(note or "").strip() != "AI生成":
            errors.append(f"第 {index} 条备注必须填写“AI生成”")

        for field, label in (
            ("manual", "手动执行通过情况"),
            ("automation", "自动化执行情况"),
        ):
            value = row.get(field, row.get(label))
            if str(value or "").strip() != "待执行":
                errors.append(f"第 {index} 条{label}必须填写“待执行”")
    return errors


def _resolve(path: str) -> Path:
    workspace = get_context().workspace
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve()
    if not target.is_relative_to(workspace):
        raise ValueError(f"路径必须在 workspace 内：{workspace}")
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在：{target}")
    return target


def _load_cases(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and isinstance(data.get("cases"), list):
        rows = data["cases"]
    elif isinstance(data, dict) and isinstance(data.get("rows"), list):
        rows = data["rows"]
    else:
        raise ValueError("JSON 必须是列表，或包含 cases/rows 列表")
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("JSON 列表里的每一项都必须是对象")
    return rows


class ValidateCasesInput(BaseModel):
    json_path: str = Field(description="测试用例 JSON 路径")
    expected_count: int = Field(
        default=0,
        ge=0,
        description="期望用例数；0 表示不校验数量",
    )


@tool(args_schema=ValidateCasesInput)
def validate_cases(json_path: str, expected_count: int = 0) -> str:
    """写 xlsx 前校验测试用例：字段、编号、模块、数量、步骤编号、备注和执行情况。"""
    try:
        source = _resolve(json_path)
        data = json.loads(source.read_text(encoding="utf-8"))
        rows = _load_cases(data)
        errors = validate_case_rows(rows, expected_count=expected_count)
        return json.dumps({
            "ok": not errors,
            "path": str(source),
            "count": len(rows),
            "ids": [
                str(_value(row, "id"))
                for row in rows
                if _is_case_row(row) and _value(row, "id") is not None
            ],
            "modules": sorted({
                str(_value(row, "module"))
                for row in rows
                if _is_case_row(row) and _value(row, "module") is not None
            }),
            "errors": errors,
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return f"错误：{type(exc).__name__}: {exc}"


__all__ = ["validate_case_rows", "validate_cases"]
