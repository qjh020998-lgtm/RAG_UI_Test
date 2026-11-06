"""xlsx 读取与 JSON 互转工具（LangChain 版）。

- read_xlsx：读取工作表、行数据、合并单元格、数据验证；
- xlsx_to_json：把第一行作为表头，后续行写成 TestSpec JSON；
- json_to_xlsx：把 JSON 列表写成 xlsx，自动识别测试用例布局。

约定：
- 读取时，合并单元格的值会填充到合并区内的每一行/列；
- 写测试用例时，按「功能模块」分组、合并 C 列，并按最终顺序重新编号。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from langchain.tools import tool
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from pydantic import BaseModel, Field

from tool.context import get_context

_CASE_HEADERS: list[str] = [
    "用例编号", "优先级", "功能模块", "测试目的", "前置条件",
    "测试步骤", "预期结果", "断言校验", "手动执行通过情况",
    "自动化执行情况", "测试人员", "备注",
]
_CASE_WIDTHS = {
    "A": 11.21, "B": 8.0, "C": 11.21, "D": 23.49, "E": 20.0,
    "F": 38.0, "G": 42.0, "H": 40.0, "I": 15.49, "J": 15.04,
    "K": 13.69, "L": 12.0,
}
_DV_FORMULA = '"通过,失败,跳过,待执行"'


def _resolve(path: str, *, must_exist: bool = False) -> Path:
    workspace = get_context().workspace
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve()
    if not target.is_relative_to(workspace):
        raise ValueError(f"路径必须在 workspace 内：{workspace}")
    if must_exist and not target.is_file():
        raise FileNotFoundError(f"文件不存在：{target}")
    return target


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _sheet_metadata(worksheet: Any) -> tuple[list[str], list[dict[str, str]]]:
    merged_obj = getattr(worksheet, "merged_cells", [])
    merged = (
        [str(item) for item in merged_obj.ranges]
        if hasattr(merged_obj, "ranges")
        else [str(item) for item in merged_obj]
    )
    validations_obj = getattr(worksheet, "data_validations", None)
    validations = [
        {"range": str(dv.sqref), "formula1": dv.formula1}
        for dv in (validations_obj.dataValidation if validations_obj else [])
    ]
    return merged, validations


def _merged_cell_values(worksheet: Any) -> dict[tuple[int, int], Any]:
    """返回合并区内每个单元格应显示的值（取合并区左上角的值）。"""
    merged_obj = getattr(worksheet, "merged_cells", [])
    ranges = merged_obj.ranges if hasattr(merged_obj, "ranges") else merged_obj
    filled: dict[tuple[int, int], Any] = {}
    for cell_range in ranges:
        anchor = worksheet.cell(cell_range.min_row, cell_range.min_col).value
        for row in range(cell_range.min_row, cell_range.max_row + 1):
            for column in range(cell_range.min_col, cell_range.max_col + 1):
                filled[(row, column)] = anchor
    return filled


def _load_rows(data: Any) -> list[dict[str, Any]]:
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


def _is_testcase(rows: list[dict[str, Any]]) -> bool:
    keys = set(rows[0])
    return {"id", "module", "purpose"} <= keys or {"用例编号", "功能模块", "测试目的"} <= keys


def _case_values(row: dict[str, Any]) -> list[Any]:
    if "用例编号" in row:
        values = []
        for header in _CASE_HEADERS:
            value = row.get(header, "")
            if header in ("手动执行通过情况", "自动化执行情况"):
                value = value or "待执行"
            elif header == "备注":
                value = value or "AI生成"
            values.append(value)
        return values
    return [
        row.get("id", ""),
        row.get("priority", ""),
        row.get("module", ""),
        row.get("purpose", ""),
        row.get("pre", ""),
        row.get("steps", ""),
        row.get("expected", ""),
        row.get("assertion", ""),
        row.get("manual", "待执行"),
        row.get("automation", "待执行"),
        row.get("tester", "@朴宰范"),
        row.get("note", "AI生成"),
    ]


_ID_PATTERN = re.compile(r"^(?P<prefix>.*?)(?P<number>\d+)$")


def _case_id_key(row: dict[str, Any]) -> str | None:
    if "用例编号" in row:
        return "用例编号"
    if "id" in row:
        return "id"
    return None


def _module_name(row: dict[str, Any]) -> str:
    return str(row.get("功能模块") or row.get("module") or "")


def _id_style(rows: list[dict[str, Any]]) -> tuple[str, int]:
    """从已有编号推导前缀与数字位数，如 采购-001 -> ("采购-", 3)。"""
    for row in rows:
        key = _case_id_key(row)
        if key is None:
            continue
        value = row.get(key)
        if not isinstance(value, str):
            continue
        match = _ID_PATTERN.match(value.strip())
        if match:
            return match.group("prefix"), max(3, len(match.group("number")))
    return "", 3


def _group_by_module(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按功能模块稳定分组：模块顺序取首次出现顺序，组内保持原顺序。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        module = _module_name(row)
        if module not in groups:
            groups[module] = []
            order.append(module)
        groups[module].append(row)
    return [row for module in order for row in groups[module]]


def _renumber(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按最终顺序重新编号，前缀与位数沿用原编号。"""
    prefix, width = _id_style(rows)
    renumbered: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        key = _case_id_key(row)
        if key is None:
            renumbered.append(row)
            continue
        renumbered.append({**row, key: f"{prefix}{index:0{width}d}"})
    return renumbered


def _write_generic(ws: Any, rows: list[dict[str, Any]], headers: list[str] | None) -> list[str]:
    columns = headers or list(rows[0])
    for col, header in enumerate(columns, start=1):
        cell = ws.cell(1, col, header)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row_index, row in enumerate(rows, start=2):
        for col, header in enumerate(columns, start=1):
            cell = ws.cell(row_index, col, _jsonable(row.get(header, "")))
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    for col_index, header in enumerate(columns, start=1):
        width = min(40, max(10, max(
            [len(str(header))] + [len(str(row.get(header, ""))) for row in rows]
        ) + 2))
        ws.column_dimensions[ws.cell(1, col_index).column_letter].width = width
    ws.freeze_panes = "A2"
    return columns


def _write_testcases(ws: Any, rows: list[dict[str, Any]]) -> list[str]:
    for col, header in enumerate(_CASE_HEADERS, start=1):
        cell = ws.cell(1, col, header)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row_index, row in enumerate(rows, start=2):
        for col, value in enumerate(_case_values(row), start=1):
            cell = ws.cell(row_index, col, _jsonable(value))
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[row_index].height = 76

    for column, width in _CASE_WIDTHS.items():
        ws.column_dimensions[column].width = width
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    # C 列按模块合并：rows 已按模块分组，这里按连续同模块区间合并
    start = 0
    while start < len(rows):
        module = _module_name(rows[start])
        end = start
        while end + 1 < len(rows) and _module_name(rows[end + 1]) == module:
            end += 1
        if module and end > start:
            ws.merge_cells(
                start_row=start + 2,
                start_column=3,
                end_row=end + 2,
                end_column=3,
            )
            ws.cell(start + 2, 3).alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True
            )
        start = end + 1

    dv = DataValidation(type="list", formula1=_DV_FORMULA, allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"I2:J{len(rows) + 1}")
    return list(_CASE_HEADERS)


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------


class ReadXlsxInput(BaseModel):
    path: str = Field(description="xlsx 文件路径")
    sheet: str = Field(default="", description="工作表名；为空则用当前活动表")
    max_rows: int = Field(default=100, ge=1, le=2000, description="最多读取行数")


@tool(args_schema=ReadXlsxInput)
def read_xlsx(path: str, sheet: str = "", max_rows: int = 100) -> str:
    """读取 xlsx 的工作表名、行数据、合并单元格和数据验证；合并区的值填充到每一行。"""
    workbook = None
    try:
        target = _resolve(path, must_exist=True)
        workbook = load_workbook(target, data_only=True)
        names = list(workbook.sheetnames)
        if sheet and sheet not in names:
            return f"错误：工作表不存在：{sheet}；可用：{names}"
        worksheet = workbook[sheet] if sheet else workbook.active
        filled = _merged_cell_values(worksheet)
        rows = []
        for row_index, row in enumerate(
            worksheet.iter_rows(
                max_row=min(max_rows, worksheet.max_row),
                values_only=True,
            ),
            start=1,
        ):
            rows.append([
                _jsonable(filled.get((row_index, column), value))
                for column, value in enumerate(row, start=1)
            ])

        merged, validations = _sheet_metadata(worksheet)
        return json.dumps({
            "path": str(target),
            "sheet": worksheet.title,
            "sheets": names,
            "rows": rows,
            "merged": merged,
            "data_validations": validations,
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return f"错误：{type(exc).__name__}: {exc}"
    finally:
        if workbook is not None:
            workbook.close()


class XlsxToJsonInput(BaseModel):
    xlsx_path: str = Field(description="输入的 xlsx 文件路径")
    json_path: str = Field(description="输出的 TestSpec JSON 路径")
    sheet: str = Field(default="", description="工作表名；为空则用当前活动表")


@tool(args_schema=XlsxToJsonInput)
def xlsx_to_json(xlsx_path: str, json_path: str, sheet: str = "") -> str:
    """读取 xlsx，把第一行作为表头，后续行写成 TestSpec JSON；合并单元格的值会填充到每一行。"""
    workbook = None
    try:
        source = _resolve(xlsx_path, must_exist=True)
        target = _resolve(json_path)
        workbook = load_workbook(source, data_only=True)
        names = list(workbook.sheetnames)
        if sheet and sheet not in names:
            return f"错误：工作表不存在：{sheet}；可用：{names}"
        worksheet = workbook[sheet] if sheet else workbook.active

        filled = _merged_cell_values(worksheet)
        headers = []
        for column in range(1, worksheet.max_column + 1):
            value = filled.get((1, column), worksheet.cell(1, column).value)
            headers.append(str(value).strip() if value is not None else f"column_{column}")

        cases = []
        for row_index, row in enumerate(
            worksheet.iter_rows(
                min_row=2,
                max_row=worksheet.max_row,
                values_only=True,
            ),
            start=2,
        ):
            values = [
                filled.get((row_index, column), value)
                for column, value in enumerate(row, start=1)
            ]
            if all(value is None or str(value).strip() == "" for value in values):
                continue
            cases.append({
                headers[index]: _jsonable(value)
                for index, value in enumerate(values)
            })

        merged, validations = _sheet_metadata(worksheet)
        spec = {
            "source": str(source),
            "sheet": worksheet.title,
            "headers": headers,
            "cases": cases,
            "merged": merged,
            "data_validations": validations,
        }
        _write_json(target, spec)
        return json.dumps({
            "json": str(target),
            "sheet": worksheet.title,
            "headers": len(headers),
            "cases": len(cases),
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return f"错误：{type(exc).__name__}: {exc}"
    finally:
        if workbook is not None:
            workbook.close()


class JsonToXlsxInput(BaseModel):
    json_path: str = Field(description="JSON 文件路径")
    xlsx_path: str = Field(description="输出 xlsx 路径")
    sheet_name: str = Field(default="Sheet1", description="工作表名")
    layout: Literal["auto", "generic", "testcase"] = Field(
        default="auto",
        description="布局：auto/generic/testcase",
    )
    headers: list[str] | None = Field(
        default=None,
        description="可选，generic 布局的列顺序",
    )


@tool(args_schema=JsonToXlsxInput)
def json_to_xlsx(
    json_path: str,
    xlsx_path: str,
    sheet_name: str = "Sheet1",
    layout: str = "auto",
    headers: list[str] | None = None,
) -> str:
    """把 JSON 列表写成 xlsx；测试用例按功能模块分组、合并同模块单元格并重新编号。"""
    try:
        source = _resolve(json_path, must_exist=True)
        target = _resolve(xlsx_path)
        data = json.loads(source.read_text(encoding="utf-8"))
        rows = _load_rows(data)

        use_testcase = layout == "testcase" or (layout == "auto" and _is_testcase(rows))
        if use_testcase:
            rows = _renumber(_group_by_module(rows))

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = sheet_name or "Sheet1"
        used_headers = (
            _write_testcases(worksheet, rows)
            if use_testcase
            else _write_generic(worksheet, rows, headers)
        )

        target.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(target)
        workbook.close()
        return json.dumps({
            "path": str(target),
            "sheet": worksheet.title,
            "layout": "testcase" if use_testcase else "generic",
            "rows": len(rows),
            "columns": len(used_headers),
            "headers": used_headers,
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return f"错误：{type(exc).__name__}: {exc}"


__all__ = ["json_to_xlsx", "read_xlsx", "xlsx_to_json"]
