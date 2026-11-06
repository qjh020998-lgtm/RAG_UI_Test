"""DomToExcel/tools/xlsx_handler.py 的读写自测。

工具约定：

- ``ReadXlsxTool`` / ``XlsxToJsonTool``：合并单元格的值填充到合并区内每一行；
- ``JsonToXlsxTool``：testcase 布局按「功能模块」稳定分组（模块顺序取首次出现顺序），
  同模块用例连续排列并合并 C 列，最后按最终顺序重新编号（前缀/位数沿用原编号）。

覆盖：

1. 读：源文件合并区填充、``max_rows`` 截断、空表头/空行/日期序列化；
2. 写：38 条用例按模块分组、合并、重编号；
3. 追加：2 条采购订单 + 2 条采购入库 → 追加后落在各自模块块末尾、合并、全文重编号；
4. 幂等：write → read → write → read 结果一致；
5. generic/auto 布局、错误路径、工具契约。

运行（根 conftest 是 UI 用例夹具，会启动浏览器）：

    pytest testself/test_xlsx_handler.py --noconftest -v

输出目录为 ``testself/selfcache/xlsx/``，每次运行前自动清空，避免读到上一次的产物。
"""
from __future__ import annotations

import asyncio
import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from DomToExcel.tools.context import ToolContext
from DomToExcel.tools.xlsx_handler import (
    JsonToXlsxTool,
    ReadXlsxTool,
    XlsxToJsonTool,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "cases" / "ERP系统测试用例 (1).xlsx"
CACHE = Path(__file__).parent / "selfcache" / "xlsx"

SPEC_JSON = CACHE / "test_spec.json"
GROUPED_XLSX = CACHE / "test_spec_grouped.xlsx"
BACK_JSON = CACHE / "test_spec_back.json"
BACK_XLSX = CACHE / "test_spec_back.xlsx"
BACK2_JSON = CACHE / "test_spec_back2.json"
APPEND_JSON = CACHE / "test_spec_append.json"
APPEND_XLSX = CACHE / "test_spec_append.xlsx"
EDGE_XLSX = CACHE / "edge_cases.xlsx"
EDGE_JSON = CACHE / "edge_cases.json"

HEADERS = [
    "用例编号", "优先级", "功能模块", "测试目的", "前置条件",
    "测试步骤", "预期结果", "断言校验", "手动执行通过情况",
    "自动化执行情况", "测试人员",
]
DV_FORMULA = '"通过,失败,跳过,待执行"'

# 源文件（sheet=采购页）的真实元数据
SOURCE_MERGED = {"C2:C11", "C12:C15", "D4:D5", "D14:D15", "E14:E15"}
SOURCE_DV = [{"range": "H30:H39 I2:I29 J2", "formula1": '"通过,失败,跳过"'}]
SOURCE_CASE_0 = {
    "用例编号": "采购-001",
    "优先级": "高",
    "功能模块": "请购单",
    "测试目的": "验证插入行功能",
    "前置条件": "进入请购单页面，点击新增",
    "测试步骤": "点击插入行",
    "预期结果": "新增一行插入记录",
    "断言校验": "明细行数量由 1 行变为 2 行",
    "手动执行通过情况": "通过",
    "自动化执行情况": "通过",
    "测试人员": "@朴宰范",
}

# 38 条用例按模块分组后的 C 列合并区域
GROUPED_MERGED = {"C2:C11", "C12:C23", "C24:C30", "C31:C36", "C37:C39"}
# 追加 2 条采购订单 + 2 条采购入库后的合并区域
APPENDED_MERGED = {"C2:C11", "C12:C25", "C26:C34", "C35:C40", "C41:C43"}

# 追加用例：2 条采购订单 + 2 条采购入库，输入编号 采购-039 ~ 采购-042
NEW_CASES = [
    {
        "用例编号": "采购-039",
        "优先级": "中",
        "功能模块": "采购订单",
        "测试目的": "验证采购订单按单据日期范围筛选",
        "前置条件": "已登录系统；列表存在多张采购订单",
        "测试步骤": "1.进入“采购管理 > 采购订单”\n2.输入单据日期范围\n3.点击“查询”",
        "预期结果": "仅显示日期范围内的采购订单",
        "断言校验": "查询结果中每条单据日期均在所选范围内",
        "手动执行通过情况": "待执行",
        "自动化执行情况": "待执行",
        "测试人员": "AI生成",
    },
    {
        "用例编号": "采购-040",
        "优先级": "中",
        "功能模块": "采购订单",
        "测试目的": "验证采购订单打印功能",
        "前置条件": "存在已审核的采购订单",
        "测试步骤": "1.勾选采购订单\n2.点击“打印”\n3.查看打印预览",
        "预期结果": "打印预览显示采购订单明细",
        "断言校验": "打印内容与订单明细一致",
        "手动执行通过情况": "待执行",
        "自动化执行情况": "待执行",
        "测试人员": "AI生成",
    },
    {
        "用例编号": "采购-041",
        "优先级": "中",
        "功能模块": "采购入库",
        "测试目的": "验证采购入库按仓库筛选",
        "前置条件": "已登录系统；存在多个有权限的仓库",
        "测试步骤": "1.进入“采购管理 > 采购入库”\n2.选择仓库\n3.点击“查询”",
        "预期结果": "仅显示所选仓库的采购入库单",
        "断言校验": "查询结果中每条入库单的仓库与筛选条件一致",
        "手动执行通过情况": "待执行",
        "自动化执行情况": "待执行",
        "测试人员": "AI生成",
    },
    {
        "用例编号": "采购-042",
        "优先级": "中",
        "功能模块": "采购入库",
        "测试目的": "验证采购入库单打印功能",
        "前置条件": "存在已审核的采购入库单",
        "测试步骤": "1.勾选采购入库单\n2.点击“打印”\n3.查看打印预览",
        "预期结果": "打印预览显示采购入库明细",
        "断言校验": "打印内容与入库明细一致",
        "手动执行通过情况": "待执行",
        "自动化执行情况": "待执行",
        "测试人员": "AI生成",
    },
]


def _run(tool: Any, **kwargs: Any) -> dict[str, Any]:
    """执行异步工具，返回解析后的 JSON；工具返回错误字符串时直接失败。"""
    raw = asyncio.run(tool.execute(**kwargs))
    if raw.startswith("错误："):
        pytest.fail(raw)
    return json.loads(raw)


def _error(tool: Any, **kwargs: Any) -> str:
    """执行异步工具，断言返回错误字符串并返回原文。"""
    raw = asyncio.run(tool.execute(**kwargs))
    assert raw.startswith("错误："), f"预期错误字符串，实际：{raw!r}"
    return raw


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


@pytest.fixture(scope="module", autouse=True)
def clean_cache() -> Path:
    """每次运行前清空 testself/selfcache/xlsx/，避免读到上一次的产物。"""
    if CACHE.exists():
        shutil.rmtree(CACHE)
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE


@pytest.fixture(scope="module")
def ctx(clean_cache: Path) -> ToolContext:
    assert SOURCE.is_file(), f"测试文件不存在：{SOURCE}"
    return ToolContext(
        config={},
        workspace=ROOT,
        run_id="xlsx_handler_self_test",
        artifacts=clean_cache,
    )


@pytest.fixture(scope="module")
def spec_file(ctx: ToolContext) -> Path:
    result = _run(
        XlsxToJsonTool(ctx),
        xlsx_path=_relative(SOURCE),
        json_path=_relative(SPEC_JSON),
        sheet="采购页",
    )
    assert result["cases"] == 38
    return SPEC_JSON


@pytest.fixture(scope="module")
def spec(spec_file: Path) -> dict[str, Any]:
    return json.loads(spec_file.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def edge_workbook(clean_cache: Path) -> Path:
    """边界工作簿：空表头、全空行、datetime/date 单元格。"""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "边界"
    worksheet.append(["名称", None, "时间"])
    worksheet.append(["a", "b", datetime(2026, 1, 2, 3, 4, 5)])
    worksheet.append([None, None, None])
    worksheet.append(["c", "d", date(2026, 1, 3)])
    workbook.save(EDGE_XLSX)
    workbook.close()
    return EDGE_XLSX


class TestXlsxHandler:
    """覆盖 xlsx_handler.py 三个工具的读写实现。"""

    # ------------------------------------------------------------------ read
    def test_read_xlsx_fills_merged_values(self, ctx: ToolContext) -> None:
        data = _run(
            ReadXlsxTool(ctx),
            path=_relative(SOURCE),
            sheet="采购页",
            max_rows=100,
        )
        assert data["sheet"] == "采购页"
        assert data["sheets"] == ["采购页", "Sheet2"]
        assert len(data["rows"]) == 39  # 表头 + 38 条用例
        assert data["rows"][0] == HEADERS
        assert data["rows"][1] == [SOURCE_CASE_0[header] for header in HEADERS]

        # C2:C11 的值填充到每一行
        assert [row[2] for row in data["rows"][1:11]] == ["请购单"] * 10
        assert [row[2] for row in data["rows"][11:23]] == ["采购订单"] * 12

        # D4:D5 / D14:D15 / E14:E15 也填充到合并区内每一行
        assert data["rows"][3][3] == data["rows"][4][3] == "验证添加行记录功能"
        assert data["rows"][13][3] == data["rows"][14][3] == "验证关联请购单功能"
        assert data["rows"][13][4] == data["rows"][14][4] == "进入采购订单页面，点击新增"

        assert set(data["merged"]) == SOURCE_MERGED
        assert data["data_validations"] == SOURCE_DV

    def test_read_xlsx_max_rows_and_default_sheet(self, ctx: ToolContext) -> None:
        truncated = _run(
            ReadXlsxTool(ctx),
            path=_relative(SOURCE),
            max_rows=5,
        )
        assert len(truncated["rows"]) == 5
        assert truncated["rows"][0] == HEADERS
        assert truncated["rows"][-1][0] == "采购-004"
        assert truncated["sheet"] == "采购页"  # 未传 sheet 时使用活动表

    # ------------------------------------------------------------ xlsx->json
    def test_xlsx_to_json_fills_merged_values(self, spec: dict[str, Any]) -> None:
        assert spec["sheet"] == "采购页"
        assert spec["headers"] == HEADERS
        assert len(spec["cases"]) == 38
        assert spec["cases"][0] == SOURCE_CASE_0

        # 合并区被填充：每个用例都带功能模块
        assert [case["功能模块"] for case in spec["cases"][:10]] == ["请购单"] * 10
        assert spec["cases"][10]["功能模块"] == "采购订单"
        assert all(case["功能模块"] for case in spec["cases"])

        assert set(spec["merged"]) == SOURCE_MERGED
        assert spec["data_validations"] == SOURCE_DV
        assert spec["source"].endswith(SOURCE.name)

    def test_xlsx_to_json_edge_cases(
        self,
        ctx: ToolContext,
        edge_workbook: Path,
    ) -> None:
        _run(
            XlsxToJsonTool(ctx),
            xlsx_path=_relative(edge_workbook),
            json_path=_relative(EDGE_JSON),
            sheet="",
        )
        edge = json.loads(EDGE_JSON.read_text(encoding="utf-8"))

        assert edge["sheet"] == "边界"
        assert edge["headers"] == ["名称", "column_2", "时间"]
        assert len(edge["cases"]) == 2  # 全空行被跳过
        assert edge["cases"][0] == {
            "名称": "a",
            "column_2": "b",
            "时间": "2026-01-02T03:04:05",
        }
        assert edge["cases"][1]["时间"] == "2026-01-03T00:00:00"
        assert edge["merged"] == []
        assert edge["data_validations"] == []

    # ------------------------------------------------------------ json->xlsx
    def test_json_to_xlsx_groups_and_renumbers(
        self,
        ctx: ToolContext,
        spec: dict[str, Any],
        spec_file: Path,
    ) -> None:
        written = _run(
            JsonToXlsxTool(ctx),
            json_path=_relative(spec_file),
            xlsx_path=_relative(GROUPED_XLSX),
            sheet_name="采购页",
            layout="testcase",
        )
        assert written["rows"] == 38
        assert written["layout"] == "testcase"
        assert written["columns"] == 12
        assert written["headers"] == [*HEADERS, "备注"]

        data = _run(
            ReadXlsxTool(ctx),
            path=_relative(GROUPED_XLSX),
            sheet="采购页",
            max_rows=100,
        )
        assert len(data["rows"]) == 39

        # 全文按最终顺序重新编号
        assert [row[0] for row in data["rows"][1:]] == [
            f"采购-{index:03d}" for index in range(1, 39)
        ]
        # 模块连续：请购单 10 / 采购订单 12 / 采购入库 7 / 采购退货 6 / 采购管理 3
        assert [row[2] for row in data["rows"][1:]] == (
            ["请购单"] * 10
            + ["采购订单"] * 12
            + ["采购入库"] * 7
            + ["采购退货"] * 6
            + ["采购管理"] * 3
        )
        assert set(data["merged"]) == GROUPED_MERGED
        assert data["data_validations"] == [
            {"range": "I2:J39", "formula1": DV_FORMULA}
        ]

        # 原用例内容未丢失（顺序/编号会变，按测试目的集合比对）
        assert sorted(row[3] for row in data["rows"][1:]) == sorted(
            case["测试目的"] for case in spec["cases"]
        )
        # 重编号示例：原 采购-036（采购入库）-> 采购-028；原 采购-033（采购管理）-> 采购-036
        by_purpose = {row[3]: row[0] for row in data["rows"][1:]}
        assert by_purpose["验证扫码录入商品"] == "采购-028"
        assert by_purpose["验证采购单据编号自动生成且唯一"] == "采购-036"

    def test_json_to_xlsx_appends_and_merges_new_cases(
        self,
        ctx: ToolContext,
        spec: dict[str, Any],
    ) -> None:
        """追加 2 条采购订单 + 2 条采购入库：加在各自模块块末尾并重新编号。"""
        payload = {**spec, "cases": [*spec["cases"], *NEW_CASES]}
        assert payload["cases"][-4:] == NEW_CASES  # 输入层面追加到 JSON 末尾
        APPEND_JSON.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        written = _run(
            JsonToXlsxTool(ctx),
            json_path=_relative(APPEND_JSON),
            xlsx_path=_relative(APPEND_XLSX),
            sheet_name="采购页",
            layout="testcase",
        )
        assert written["rows"] == 42

        data = _run(
            ReadXlsxTool(ctx),
            path=_relative(APPEND_XLSX),
            sheet="采购页",
            max_rows=100,
        )
        assert len(data["rows"]) == 43
        rows = data["rows"][1:]

        # 42 条用例全文连续编号
        assert [row[0] for row in rows] == [
            f"采购-{index:03d}" for index in range(1, 43)
        ]
        # 模块分组：采购订单 12+2，采购入库 7+2
        assert [row[2] for row in rows] == (
            ["请购单"] * 10
            + ["采购订单"] * 14
            + ["采购入库"] * 9
            + ["采购退货"] * 6
            + ["采购管理"] * 3
        )
        assert set(data["merged"]) == APPENDED_MERGED
        assert data["data_validations"] == [
            {"range": "I2:J43", "formula1": DV_FORMULA}
        ]

        # 新增用例位于各自模块块的末尾，并拿到重编后的编号
        order_rows = [row for row in rows if row[2] == "采购订单"]
        inbound_rows = [row for row in rows if row[2] == "采购入库"]
        assert [row[0] for row in order_rows[-2:]] == ["采购-023", "采购-024"]
        assert [row[0] for row in inbound_rows[-2:]] == ["采购-032", "采购-033"]
        assert [row[3] for row in order_rows[-2:]] == [
            NEW_CASES[0]["测试目的"],
            NEW_CASES[1]["测试目的"],
        ]
        assert [row[3] for row in inbound_rows[-2:]] == [
            NEW_CASES[2]["测试目的"],
            NEW_CASES[3]["测试目的"],
        ]

        # 除编号外的 10 个字段与输入完全一致
        by_purpose = {row[3]: row for row in rows}
        for case in NEW_CASES:
            row = by_purpose[case["测试目的"]]
            for column, header in enumerate(HEADERS):
                if header == "用例编号":
                    continue
                assert row[column] == case[header]

        # 原 38 条 + 新增 4 条都在（按测试目的多重集比对）
        assert sorted(row[3] for row in rows) == sorted(
            [case["测试目的"] for case in spec["cases"]]
            + [case["测试目的"] for case in NEW_CASES]
        )
        # 分组后新用例在模块块末尾，文件最后一行仍是原「采购管理」模块
        assert data["rows"][-1][2] == "采购管理"

    def test_json_to_xlsx_roundtrip_is_idempotent(
        self,
        ctx: ToolContext,
        spec_file: Path,
    ) -> None:
        _run(
            JsonToXlsxTool(ctx),
            json_path=_relative(spec_file),
            xlsx_path=_relative(GROUPED_XLSX),
            sheet_name="采购页",
            layout="testcase",
        )
        _run(
            XlsxToJsonTool(ctx),
            xlsx_path=_relative(GROUPED_XLSX),
            json_path=_relative(BACK_JSON),
            sheet="采购页",
        )
        _run(
            JsonToXlsxTool(ctx),
            json_path=_relative(BACK_JSON),
            xlsx_path=_relative(BACK_XLSX),
            sheet_name="采购页",
            layout="testcase",
        )
        _run(
            XlsxToJsonTool(ctx),
            xlsx_path=_relative(BACK_XLSX),
            json_path=_relative(BACK2_JSON),
            sheet="采购页",
        )

        back = json.loads(BACK_JSON.read_text(encoding="utf-8"))
        back2 = json.loads(BACK2_JSON.read_text(encoding="utf-8"))
        assert [case["用例编号"] for case in back["cases"]] == [
            f"采购-{index:03d}" for index in range(1, 39)
        ]
        assert back["cases"] == back2["cases"]
        assert back["merged"] == back2["merged"]
        assert back["data_validations"] == back2["data_validations"]
        assert back["headers"] == back2["headers"]

    def test_json_to_xlsx_generic_layout(self, ctx: ToolContext) -> None:
        json_path = CACHE / "generic.json"
        xlsx_path = CACHE / "generic.xlsx"
        json_path.write_text(
            json.dumps(
                [{"姓名": "张三", "数量": 3}, {"姓名": "李四", "数量": 5}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        written = _run(
            JsonToXlsxTool(ctx),
            json_path=_relative(json_path),
            xlsx_path=_relative(xlsx_path),
            layout="generic",
            headers=["姓名", "数量"],
        )
        assert written["layout"] == "generic"
        assert written["rows"] == 2
        assert written["columns"] == 2
        assert written["headers"] == ["姓名", "数量"]

        data = _run(ReadXlsxTool(ctx), path=_relative(xlsx_path), max_rows=10)
        assert data["rows"] == [["姓名", "数量"], ["张三", 3], ["李四", 5]]

    def test_json_to_xlsx_auto_layout(self, ctx: ToolContext) -> None:
        json_path = CACHE / "auto.json"
        xlsx_path = CACHE / "auto.xlsx"
        json_path.write_text(
            json.dumps(
                [{"id": "TC-1", "module": "采购订单", "purpose": "验证新增"}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        written = _run(
            JsonToXlsxTool(ctx),
            json_path=_relative(json_path),
            xlsx_path=_relative(xlsx_path),
            layout="auto",
        )
        assert written["layout"] == "testcase"
        assert written["columns"] == 12

        data = _run(ReadXlsxTool(ctx), path=_relative(xlsx_path), max_rows=10)
        # 英文键走 testcase 分支：编号沿用 TC- 前缀并补零，空字段读回 None，其余取默认值
        assert data["rows"][1] == [
            "TC-001", None, "采购订单", "验证新增", None, None, None, None,
            "待执行", "待执行", "@朴宰范", "AI生成",
        ]

    def test_json_to_xlsx_rows_key(self, ctx: ToolContext) -> None:
        json_path = CACHE / "rows.json"
        xlsx_path = CACHE / "rows.xlsx"
        json_path.write_text(
            json.dumps({"rows": [{"姓名": "王五"}]}, ensure_ascii=False),
            encoding="utf-8",
        )

        written = _run(
            JsonToXlsxTool(ctx),
            json_path=_relative(json_path),
            xlsx_path=_relative(xlsx_path),
        )
        assert written["layout"] == "generic"
        assert written["rows"] == 1
        assert written["headers"] == ["姓名"]

    @pytest.mark.parametrize(
        "payload",
        [{"foo": []}, {}, [], [1, 2]],
        ids=["no-cases-key", "empty-dict", "empty-list", "non-dict-items"],
    )
    def test_json_to_xlsx_rejects_invalid_payload(
        self,
        ctx: ToolContext,
        payload: Any,
    ) -> None:
        json_path = CACHE / "invalid.json"
        json_path.write_text(json.dumps(payload), encoding="utf-8")

        message = _error(
            JsonToXlsxTool(ctx),
            json_path=_relative(json_path),
            xlsx_path=_relative(CACHE / "invalid.xlsx"),
        )
        assert "JSON" in message and "列表" in message

    # ----------------------------------------------------------------- error
    def test_error_paths(self, ctx: ToolContext) -> None:
        assert "FileNotFoundError" in _error(ReadXlsxTool(ctx), path="missing.xlsx")
        assert "工作表不存在" in _error(
            ReadXlsxTool(ctx),
            path=_relative(SOURCE),
            sheet="不存在",
        )
        assert "workspace" in _error(
            ReadXlsxTool(ctx),
            path="../outside.xlsx",
        )
        assert "FileNotFoundError" in _error(
            XlsxToJsonTool(ctx),
            xlsx_path="missing.xlsx",
            json_path=_relative(CACHE / "x.json"),
        )
        assert "FileNotFoundError" in _error(
            JsonToXlsxTool(ctx),
            json_path="missing.json",
            xlsx_path=_relative(CACHE / "x.xlsx"),
        )

    def test_tool_contract(self, ctx: ToolContext) -> None:
        read_tool = ReadXlsxTool(ctx)
        assert read_tool.read_only is True
        assert read_tool.exclusive is False
        assert read_tool.concurrency_safe is True

        for tool in (XlsxToJsonTool(ctx), JsonToXlsxTool(ctx)):
            assert tool.read_only is False
            assert tool.exclusive is True
            assert tool.concurrency_safe is False

        for tool in (read_tool, XlsxToJsonTool(ctx), JsonToXlsxTool(ctx)):
            assert tool.definition_errors() == []
