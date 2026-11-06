"""测试用例 JSON 校验工具自测。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from DomToExcel.tools.case_validator import ValidateCasesTool
from DomToExcel.tools.context import ToolContext

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent / "selfcache"


def _write_cases(name: str, cases: list[dict]) -> Path:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _validate(path: Path, expected_count: int = 0) -> dict:
    ctx = ToolContext(config={}, workspace=ROOT, run_id="validator_test", artifacts=OUT)
    raw = asyncio.run(
        ValidateCasesTool(ctx).execute(
            json_path=str(path.relative_to(ROOT)),
            expected_count=expected_count,
        )
    )
    assert not raw.startswith("错误："), raw
    return json.loads(raw)


def test_valid_cases() -> None:
    cases = [
        {
            "id": "CGDD-001",
            "priority": "高",
            "module": "采购订单",
            "purpose": "列表加载",
            "pre": "已登录并进入采购订单",
            "steps": "1. 打开采购订单\n2. 查看表头",
            "expected": "列表显示",
            "assertion": "表头包含供应商",
            "manual": "待执行",
            "automation": "待执行",
            "note": "AI生成",
        },
        {
            "id": "CGDD-002",
            "priority": "中",
            "module": "采购订单",
            "purpose": "新增必填校验",
            "pre": "已打开新增弹窗",
            "steps": "1. 直接点击保存\n2. 查看提示",
            "expected": "提示必填",
            "assertion": "出现必填提示",
            "manual": "待执行",
            "automation": "待执行",
            "note": "AI生成",
        },
    ]
    path = _write_cases("valid_cases.json", cases)
    result = _validate(path, expected_count=2)
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["ids"] == ["CGDD-001", "CGDD-002"]
    assert result["modules"] == ["采购订单"]
    assert result["errors"] == []


def test_duplicate_and_missing_fields() -> None:
    cases = [
        {
            "id": "CGDD-001",
            "priority": "高",
            "module": "采购订单",
            "purpose": "列表加载",
            "pre": "已登录",
            "steps": "1. 打开",
            "expected": "显示",
            "assertion": "存在",
        },
        {
            "id": "CGDD-001",
            "priority": "",
            "module": "",
            "purpose": "",
            "pre": "",
            "steps": "",
            "expected": "",
            "assertion": "",
        },
    ]
    path = _write_cases("invalid_cases.json", cases)
    result = _validate(path, expected_count=3)
    assert result["ok"] is False
    assert any("数量" in item for item in result["errors"])
    assert any("重复" in item for item in result["errors"])
    assert any("缺少优先级" in item for item in result["errors"])


def test_chinese_headers_are_supported() -> None:
    cases = [{
        "用例编号": "CGDD-001",
        "优先级": "高",
        "功能模块": "采购订单",
        "测试目的": "列表加载",
        "前置条件": "已登录",
        "测试步骤": "1. 打开\n2. 查看",
        "预期结果": "显示",
        "断言校验": "存在",
        "手动执行通过情况": "待执行",
        "自动化执行情况": "待执行",
        "备注": "AI生成",
    }]
    path = _write_cases("chinese_cases.json", cases)
    result = _validate(path, expected_count=1)
    assert result["ok"] is True
    assert result["count"] == 1


def test_steps_and_fixed_fields() -> None:
    cases = [{
        "id": "CGDD-001",
        "priority": "高",
        "module": "采购订单",
        "purpose": "列表加载",
        "pre": "已登录",
        "steps": "1. 打开\n3. 跳步",
        "expected": "显示",
        "assertion": "存在",
        "manual": "通过",
        "automation": "待补充",
        "note": "人工整理",
    }]
    path = _write_cases("bad_steps_cases.json", cases)
    result = _validate(path, expected_count=1)
    assert result["ok"] is False
    assert any("测试步骤编号" in item for item in result["errors"])
    assert any("AI生成" in item for item in result["errors"])
    assert any("待执行" in item for item in result["errors"])
