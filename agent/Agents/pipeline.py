"""测试代码生成编排：Excel → page 类 → testcases。

流程
----
1. 读取 Excel 指定 sheet，只保留 ``自动化执行情况 == 待执行`` 的用例；
2. 检查该 sheet 是否有对应 page 类；没有则**截断**并提示人工补充；
3. 逐条用例解析步骤（``1. xxx\n2. xxx``），与 page 类里已有的
   ``@allure.step("...")`` 描述比对；缺失的步骤才需要新增；
4. 缺失步骤：
   - 控件缓存不存在 -> 调 ``ComponentAgent`` 抓取；
   - 调 ``StepAgent`` 生成 page 方法并追加；
5. 调 ``FinalAgent`` 生成 pytest 方法并追加到 ``testcases/test_*.py``。

安全
----
默认 dry-run（``--apply`` 才写文件），避免误改 page 类和测试文件。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.Agents.Base_Agent import PROJECT_ROOT, log
from agent.Agents.component_agent import ComponentAgent
from agent.Agents.final_agent import FinalAgent
from agent.Agents.step_agent import StepAgent
from agent.tools.excel_utils import XLSX_DEFAULT, load_pending_cases
from agent.tools.page_utils import (
    append_code,
    covered,
    extract_step_descriptions,
    load_control_cache,
)

# sheet -> page 类 / 测试文件 / fixture
SHEET_CONFIG: dict[str, dict] = {
    "采购页": {
        "module": "采购管理",
        "page_name": "采购订单",
        "page_file": "page/purchase_page.py",
        "page_class": "PurchasePage",
        "test_file": "testcases/test_purchase.py",
        "fixture": "purchase_apply_page",
        "url": "http://39.101.122.147:3000/bill/purchase_order",
        "storage_state": "artifacts/auth/testcases_test_purchase.py_TestPurchase.json",
    },
    "零售页": {
        "module": "零售管理",
        "page_name": "零售出库",
        "page_file": "page/retail_page.py",
        "page_class": "RetailPage",
        "test_file": "testcases/test_retail.py",
        "fixture": "retail_out_page",
        "url": "http://39.101.122.147:3000/bill/retail_out",
        "storage_state": "artifacts/auth/testcases_test_retail.py_TestRetail.json",
    },
}


# ---------------------------------------------------------------------------
# 主编排
# ---------------------------------------------------------------------------


def run(
    sheet: str,
    *,
    xlsx: Path = XLSX_DEFAULT,
    apply: bool = False,
    limit: int = 0,
) -> dict:
    config = SHEET_CONFIG.get(sheet)
    if not config:
        raise SystemExit(f"[截断] 未配置的 sheet：{sheet}")

    page_path = PROJECT_ROOT / config["page_file"]
    if not page_path.exists():
        raise SystemExit(
            f"[截断] sheet「{sheet}」没有对应 page 类：{page_path}。"
            f"请人工先生成 {config['page_class']} 与该 sheet 的 xlsx 映射。"
        )

    cases = load_pending_cases(xlsx, sheet)
    if limit:
        cases = cases[:limit]
    log.info("sheet=%s 待执行用例：%s 条", sheet, len(cases))
    if not cases:
        return {"sheet": sheet, "cases": 0, "missing_steps": 0, "page_written": False, "test_written": False}

    page_source = page_path.read_text(encoding="utf-8")
    descriptions = extract_step_descriptions(page_source)

    missing_by_case: list[tuple[dict, list[str]]] = []
    for case in cases:
        missing = [step for step in case["steps"] if not covered(step, descriptions)]
        if missing:
            missing_by_case.append((case, missing))

    page_written = False
    if missing_by_case:
        cache = load_control_cache(config["module"], config["page_name"], "create_modal")
        if not cache:
            log.info("[Agent] 控件缓存缺失，调用控件获取Agent")
            ComponentAgent().ensure_cache(
                config["module"],
                config["page_name"],
                state="create_modal",
                url=config["url"],
                storage_state=str(PROJECT_ROOT / config["storage_state"]),
            )
            cache = load_control_cache(config["module"], config["page_name"], "create_modal")

        all_steps = [step for _, missing in missing_by_case for step in missing]
        code = StepAgent().generate_methods(
            config["page_class"],
            page_source,
            all_steps,
            cache,
            module=config["module"],
        )
        if apply:
            append_code(page_path, code)
            page_written = True
        else:
            log.info("[dry-run] StepAgent 输出预览：\n%s", code[:1000])

    test_path = PROJECT_ROOT / config["test_file"]
    existing = test_path.read_text(encoding="utf-8") if test_path.exists() else ""
    test_code = FinalAgent().generate_tests(
        sheet=sheet,
        page_class=config["page_class"],
        page_source=page_source,
        cases=cases,
        existing_tests=existing[-6000:],
        fixture=config["fixture"],
    )
    if apply:
        append_code(test_path, test_code)
        test_written = True
    else:
        log.info("[dry-run] FinalAgent 输出预览：\n%s", test_code[:1000])
        test_written = False

    return {
        "sheet": sheet,
        "cases": len(cases),
        "missing_steps": sum(len(missing) for _, missing in missing_by_case),
        "page_written": page_written,
        "test_written": test_written,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Excel 用例 → page 方法 → testcases 代码")
    parser.add_argument("--sheet", required=True, help="工作表名，如 采购页 / 零售页")
    parser.add_argument("--xlsx", default=str(XLSX_DEFAULT), help="Excel 路径")
    parser.add_argument("--apply", action="store_true", help="真正写文件（默认 dry-run）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条用例")
    args = parser.parse_args(argv)

    result = run(args.sheet, xlsx=Path(args.xlsx), apply=args.apply, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
