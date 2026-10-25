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

流式
----
三个 Agent 默认实时打印思考 / 生成 / 工具调用过程（``AGENT_STREAM=true``）。
CLI 传 ``--no-stream`` 可关闭，等待完整结果后再一次性输出。

安全
----
默认 dry-run（``--apply`` 才写文件），避免误改 page 类和测试文件。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from agent.Agents.Base_Agent import PROJECT_ROOT, AgentSettings, log
from agent.Agents.component_agent import ComponentAgent
from agent.Agents.final_agent import FinalAgent
from agent.Agents.step_agent import StepAgent
from agent.tools.excel_utils import XLSX_DEFAULT, load_pending_cases
from agent.tools.page_utils import (
    append_code,
    covered,
    load_control_cache,
)
from agent.tools.code_validator import missing_methods, validate
from agent.tools.step_registry import load_registry, register_locators, write_registry
from data_loader import load_yaml

# sheet -> page 类 / 测试文件 / fixture
SHEET_CONFIG: dict[str, dict] = {
    "采购页": {
        "module": "采购管理",
        "page_name": "采购订单",
        "page_key": "purchase_page",
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
        "page_key": "retail_page",
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


def _extract_missing_controls(code: str) -> list[str]:
    """从 StepAgent 输出里提取 ``# 需补充控件: xxx``。"""
    return re.findall(r"#\s*需补充控件[:：]\s*(.+)", code)


def _refresh(page_key: str):
    """写完 page 文件后刷新 step.yaml，返回最新注册表。"""
    write_registry()
    steps, locators = load_registry()
    registered_steps = {**steps.get("base_page", {}), **steps.get(page_key, {})}
    return registered_steps, locators.get(page_key, {}), steps, locators


def run(
    sheet: str,
    *,
    xlsx: Path = XLSX_DEFAULT,
    apply: bool = False,
    limit: int = 0,
    stream: bool | None = None,
) -> dict:
    """执行一次生成编排。

    ``stream=None`` 时跟随 ``AGENT_STREAM``；``False`` 时不打印中间过程。
    """
    config = SHEET_CONFIG.get(sheet)
    if not config:
        raise SystemExit(f"[截断] 未配置的 sheet：{sheet}")

    stream_enabled = AgentSettings.from_env().stream if stream is None else stream

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

    # 读注册表，不再把整份 page 源码塞进 prompt
    steps_registry, locators_registry = load_registry()
    page_key = config["page_key"]
    registered_steps = {
        **steps_registry.get("base_page", {}),
        **steps_registry.get(page_key, {}),
    }
    registered_locators = locators_registry.get(page_key, {})
    cache: list[dict] = []
    descriptions = [
        (meta.get("step", ""), name) for name, meta in registered_steps.items()
    ]

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
                stream=stream,
            )
            cache = load_control_cache(config["module"], config["page_name"], "create_modal")

        all_steps = [step for _, missing in missing_by_case for step in missing]
        code = StepAgent().generate_methods(
            config["page_class"],
            all_steps,
            cache,
            registered_steps=registered_steps,
            registered_locators=registered_locators,
            module=config["module"],
            stream=stream,
        )

        # StepAgent 报告缺少控件 -> 控件获取Agent -> 注册 locator.yaml -> 重试
        missing_controls = _extract_missing_controls(code)
        if missing_controls:
            log.info("[Agent] StepAgent 报告缺少控件：%s", missing_controls)
            ComponentAgent().ensure_cache(
                config["module"],
                config["page_name"],
                state="create_modal",
                url=config["url"],
                storage_state=str(PROJECT_ROOT / config["storage_state"]),
                stream=stream,
            )
            cache = load_control_cache(config["module"], config["page_name"], "create_modal")
            added = register_locators(page_key, cache)
            log.info("[Agent] 已注册 %s 个控件到 locator.yaml", added)

            steps_registry, locators_registry = load_registry()
            registered_steps = {
                **steps_registry.get("base_page", {}),
                **steps_registry.get(page_key, {}),
            }
            registered_locators = locators_registry.get(page_key, {})
            code = StepAgent().generate_methods(
                config["page_class"],
                all_steps,
                cache,
                registered_steps=registered_steps,
                registered_locators=registered_locators,
                module=config["module"],
                stream=stream,
            )

        if apply:
            append_code(page_path, code)
            page_written = True
            registered_steps, registered_locators, steps_registry, locators_registry = _refresh(
                page_key
            )
        elif stream_enabled:
            log.info("[dry-run] StepAgent 输出已在上方流式展示，未写入文件")
        else:
            log.info("[dry-run] StepAgent 输出预览：\n%s", code[:1000])

    test_path = PROJECT_ROOT / config["test_file"]
    existing = test_path.read_text(encoding="utf-8") if test_path.exists() else ""
    test_code = FinalAgent().generate_tests(
        sheet=sheet,
        page_class=config["page_class"],
        cases=cases,
        registered_steps=registered_steps,
        registered_fixtures=steps_registry.get("fixtures", {}),
        registered_controls=locators_registry.get("controls", {}),
        purchase_data=load_yaml("purchase.yaml"),
        existing_tests=existing[-4000:],
        fixture=config["fixture"],
        stream=stream,
    )

    # 校验：FinalAgent 调用了注册表里没有的方法 -> 回头让 StepAgent 补 -> 重新生成
    errors = validate(test_code, set(registered_steps), registered_steps)
    if errors:
        log.warning("[校验] FinalAgent 输出有问题：%s", errors)
        missing = missing_methods(test_code, set(registered_steps))
        if apply and missing:
            log.info("[校验] 调用 StepAgent 补齐 %s 个缺失 step：%s", len(missing), missing)
            patch = StepAgent().generate_methods(
                config["page_class"],
                missing,
                cache,
                registered_steps=registered_steps,
                registered_locators=registered_locators,
                module=config["module"],
                stream=stream,
            )
            append_code(page_path, patch)
            registered_steps, registered_locators, steps_registry, locators_registry = _refresh(
                page_key
            )
            test_code = FinalAgent().generate_tests(
                sheet=sheet,
                page_class=config["page_class"],
                cases=cases,
                registered_steps=registered_steps,
                registered_fixtures=steps_registry.get("fixtures", {}),
                registered_controls=locators_registry.get("controls", {}),
                purchase_data=load_yaml("purchase.yaml"),
                existing_tests=existing[-4000:],
                fixture=config["fixture"],
                stream=stream,
            )
            errors = validate(test_code, set(registered_steps), registered_steps)
            log.info("[校验] 补齐后：%s", errors or "通过")
        elif missing:
            log.info("[dry-run] 缺失 step：%s", missing)
    if apply:
        append_code(test_path, test_code)
        test_written = True
    else:
        test_written = False
        if stream_enabled:
            log.info("[dry-run] FinalAgent 输出已在上方流式展示，未写入文件")
        else:
            log.info("[dry-run] FinalAgent 输出预览：\n%s", test_code[:1000])

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
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="关闭流式输出，等待 Agent 完整返回后再打印",
    )
    args = parser.parse_args(argv)

    result = run(
        args.sheet,
        xlsx=Path(args.xlsx),
        apply=args.apply,
        limit=args.limit,
        stream=not args.no_stream,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
