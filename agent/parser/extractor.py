"""把 Excel 行/原始表格文本转换成 TestSpec。

这里集中处理所有“表格到模型”的脏活：

- 中文列名 → 英文字段；
- 多行文本 → list[str]；
- 合并单元格造成的空模块 → 继承上一行；
- 中文/英文优先级归一化；
- 空状态、AI 标记归一化；
- JSON / TSV / CSV 原始文本解析。

模型本身只保留字段定义。
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable, Mapping, Sequence

from agent.models import InputSource, Priority, TestCase, TestSpec


EXCEL_COLUMNS = (
    "用例编号",
    "优先级",
    "功能模块",
    "测试目的",
    "前置条件",
    "测试步骤",
    "预期结果",
    "断言校验",
    "手动执行通过情况",
    "自动化执行情况",
    "测试人员",
    "AI标记",
)


class ExtractionError(ValueError):
    """输入无法转换成 TestSpec 时抛出。"""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _split_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.replace("\r\n", "\n").replace("\r", "\n")
        return [line.strip() for line in text.split("\n") if line.strip()]
    if isinstance(value, Sequence):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _priority(value: Any) -> Priority:
    text = _text(value)
    mapping = {
        "高": Priority.HIGH,
        "HIGH": Priority.HIGH,
        "CRITICAL": Priority.HIGH,
        "中": Priority.MEDIUM,
        "MEDIUM": Priority.MEDIUM,
        "低": Priority.LOW,
        "LOW": Priority.LOW,
    }
    return mapping.get(text.upper() if text.isascii() else text, Priority.MEDIUM)


def _ai_mark(value: Any) -> str:
    if isinstance(value, bool):
        return "AI生成" if value else ""
    return _text(value)


def _get(data: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def _case_from_mapping(
    data: Mapping[str, Any],
    *,
    module: str = "",
) -> TestCase:
    case_id = _text(_get(data, "用例编号", "id"))
    if not case_id:
        raise ExtractionError(f"测试用例缺少编号：{data!r}")

    return TestCase(
        id=case_id,
        priority=_priority(_get(data, "优先级", "priority")),
        module=_text(_get(data, "功能模块", "module")) or module,
        purpose=_text(_get(data, "测试目的", "purpose", "title")),
        preconditions=_split_lines(_get(data, "前置条件", "preconditions")),
        steps=_split_lines(_get(data, "测试步骤", "steps")),
        expected_result=_text(_get(data, "预期结果", "expected_result")),
        assertions=_text(_get(data, "断言校验", "assertions")),
        manual_status=_text(
            _get(data, "手动执行通过情况", "manual_status")
        ) or "待执行",
        automation_status=_text(
            _get(data, "自动化执行情况", "automation_status")
        ) or "待生成",
        tester=_text(_get(data, "测试人员", "tester")),
        ai_mark=_ai_mark(_get(data, "AI标记", "ai_mark")),
    )


def _case_from_row(row: Sequence[Any], *, module: str) -> TestCase:
    data = {
        name: row[index] if index < len(row) else None
        for index, name in enumerate(EXCEL_COLUMNS)
    }
    return _case_from_mapping(data, module=module)


def extract_from_excel_rows(
    rows: Iterable[Sequence[Any] | Mapping[str, Any]],
    *,
    sheet: str,
    source_id: str | None = None,
    workbook_path: str | None = None,
    title: str | None = None,
    module: str | None = None,
    source: InputSource = InputSource.EXCEL,
    raw_text: str = "",
) -> TestSpec:
    """把 Excel 数据行转换为 TestSpec。

    ``rows`` 不包含表头。模块列因合并单元格为空时，自动继承上一行的模块。
    """
    cases: list[TestCase] = []
    current_module = module or ""

    for row in rows:
        if isinstance(row, Mapping):
            if not _text(_get(row, "用例编号", "id")):
                continue
            row_module = _text(_get(row, "功能模块", "module"))
            case = _case_from_mapping(row, module=current_module)
        else:
            if not row or not _text(row[0]):
                continue
            row_module = _text(row[2]) if len(row) > 2 else ""
            case = _case_from_row(row, module=current_module)

        if row_module:
            current_module = row_module
            case.module = row_module
        if not case.id:
            continue
        cases.append(case)

    modules = sorted({case.module for case in cases if case.module})
    return TestSpec(
        source=source,
        source_id=source_id or workbook_path or sheet,
        title=title or f"{sheet} — ERP 测试用例",
        sheet=sheet,
        module=", ".join(modules) if modules else (module or None),
        test_cases=cases,
        raw_text=raw_text,
    )


def _spec_from_json(
    data: Any,
    *,
    raw_text: str,
    source: InputSource,
    source_id: str,
    title: str | None,
    sheet: str | None,
    module: str | None,
) -> TestSpec:
    if isinstance(data, list):
        cases_data = data
        spec_data: Mapping[str, Any] = {}
    elif isinstance(data, Mapping):
        spec_data = data
        cases_data = data.get("test_cases") or data.get("cases")
        if cases_data is None and ("用例编号" in data or "id" in data):
            cases_data = [data]
    else:
        raise ExtractionError("JSON 必须是 TestSpec 对象、测试用例对象或测试用例数组")

    if not isinstance(cases_data, list):
        raise ExtractionError("TestSpec.test_cases 必须是数组")

    cases = [
        _case_from_mapping(item)
        for item in cases_data
        if isinstance(item, Mapping)
    ]
    source_value = str(spec_data.get("source") or source.value).lower()
    try:
        spec_source = InputSource(source_value)
    except ValueError:
        spec_source = source

    modules = sorted({case.module for case in cases if case.module})
    return TestSpec(
        source=spec_source,
        source_id=_text(spec_data.get("source_id")) or source_id,
        title=_text(spec_data.get("title")) or title or "未命名测试用例",
        sheet=_text(spec_data.get("sheet")) or sheet,
        module=_text(spec_data.get("module")) or module or (", ".join(modules) or None),
        test_cases=cases,
        raw_text=raw_text,
    )


def _read_delimited_rows(raw_text: str) -> list[dict[str, Any]]:
    for delimiter in ("\t", ",", "|"):
        reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
        rows = [list(row) for row in reader if any(str(cell).strip() for cell in row)]
        if len(rows) < 2:
            continue

        header = [cell.strip() for cell in rows[0]]
        if len(header) >= 12 and not header[11]:
            header[11] = "AI标记"
        if "用例编号" not in header and "id" not in [h.lower() for h in header]:
            continue

        records = []
        for row in rows[1:]:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            records.append(dict(zip(header, row)))
        return records
    return []


def extract_test_spec(
    raw_text: str,
    *,
    source: InputSource = InputSource.TEXT,
    source_id: str = "text",
    title: str | None = None,
    sheet: str | None = None,
    module: str | None = None,
) -> TestSpec:
    """把 raw_text 转换为 TestSpec。

    支持：

    - JSON：TestSpec 对象、单条测试用例对象、测试用例数组；
    - 带表头的 TSV/CSV/竖线分隔文本。

    自然语言自由文本目前不在这里硬解析，应由后续 LLM extractor 处理；
    这样模型层和表格解析层不会互相污染。
    """
    text = (raw_text or "").strip()
    if not text:
        raise ExtractionError("raw_text 为空")

    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"JSON 解析失败：{exc}") from exc
        return _spec_from_json(
            data,
            raw_text=raw_text,
            source=source,
            source_id=source_id,
            title=title,
            sheet=sheet,
            module=module,
        )

    records = _read_delimited_rows(text)
    if records:
        return extract_from_excel_rows(
            records,
            sheet=sheet or "raw_text",
            source_id=source_id,
            title=title,
            module=module,
            source=source,
            raw_text=raw_text,
        )

    raise ExtractionError(
        "无法识别 raw_text：请提供 JSON 或带表头的 TSV/CSV；"
        "自然语言请交给 LLM extractor"
    )


def extract_to_json(raw_text: str, **kwargs: Any) -> str:
    """raw_text → TestSpec → JSON 字符串。"""
    return extract_test_spec(raw_text, **kwargs).model_dump_json(indent=2)


__all__ = [
    "EXCEL_COLUMNS",
    "ExtractionError",
    "extract_from_excel_rows",
    "extract_test_spec",
    "extract_to_json",
]
