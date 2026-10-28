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
import shutil
from datetime import datetime
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

# ---------------------------------------------------------------------------
# 生成副本管理
# ---------------------------------------------------------------------------

GEN_RUNS_DIR = PROJECT_ROOT / "cache" / "gen_runs"


def _create_run_dir(sheet: str, label: str = "") -> Path:
    """在 `cache/gen_runs/` 下创建带时间戳的副本目录。

    目录命名：`<sheet>_<YYYYMMDD_HHMMSS>` 或 `<label>`（自定义）。
    返回目录路径。
    """
    GEN_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if label:
        run_name = label
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = f"{sheet}_{timestamp}"
    run_dir = GEN_RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _copy_to_run_dir(run_dir: Path, src: Path, suffix: str = "") -> Path:
    """把原文件复制到 run_dir，返回副本路径。`suffix` 插入文件名末尾。"""
    if not src.exists():
        raise FileNotFoundError(f"源文件不存在：{src}")
    dst_name = src.stem + suffix + src.suffix
    dst = run_dir / dst_name
    shutil.copy2(src, dst)
    return dst


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


def _existing_case_ids(test_code: str) -> set[str]:
    """从已有测试文件提取 ``@allure.story("采购-001 ...")`` 的 case id。"""
    return set(re.findall(r"@allure\.story\(\s*['\"]([^'\"]+)", test_code))


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
    label: str = "",
    test_file: str = "",
    force: bool = False,
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

    # 生成副本：apply 模式下写入副本，不污染原文件
    run_dir: Path | None = None
    page_copy_path: Path | None = None
    test_copy_path: Path | None = None
    if apply:
        run_dir = _create_run_dir(sheet, label=label)
        page_copy_path = _copy_to_run_dir(run_dir, page_path)
        test_src = PROJECT_ROOT / config["test_file"]
        if test_src.exists():
            test_copy_path = _copy_to_run_dir(run_dir, test_src)
        log.info("[副本] 生成副本目录：%s", run_dir)
        log.info("[副本] page 副本：%s", page_copy_path)
        if test_copy_path:
            log.info("[副本] test 副本：%s", test_copy_path)
        # 后续写入指向副本
        page_path = page_copy_path

    cases = load_pending_cases(xlsx, sheet)
    if limit:
        cases = cases[:limit]
    log.info("sheet=%s 待执行用例：%s 条", sheet, len(cases))
    if not cases:
        return {"sheet": sheet, "cases": 0, "missing_steps": 0, "page_written": False, "test_written": False}

    # 已有测试按 case id 去重：同一个 Excel 用例只生成一次，避免多批 append 成 87 条
    test_path_original = PROJECT_ROOT / config["test_file"]
    existing = (
        test_path_original.read_text(encoding="utf-8")
        if test_path_original.exists()
        else ""
    )
    if not force:
        stories = _existing_case_ids(existing)
        kept = [
            case
            for case in cases
            if not any(story.startswith(str(case["id"])) for story in stories)
        ]
        if len(kept) != len(cases):
            log.info(
                "[去重] 已有测试覆盖 %s 条用例，本轮跳过，仅生成 %s 条",
                len(cases) - len(kept),
                len(kept),
            )
        cases = kept
    if not cases:
        log.info("[去重] 本轮用例均已有测试，未生成新代码")
        return {
            "sheet": sheet,
            "cases": 0,
            "missing_steps": 0,
            "page_written": False,
            "test_written": False,
            "skipped_existing": True,
        }

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

    test_path = test_copy_path if (apply and test_copy_path) else test_path_original
    test_code = FinalAgent().generate_tests(
        sheet=sheet,
        page_class=config["page_class"],
        cases=cases,
        registered_steps=registered_steps,
        registered_fixtures=steps_registry.get("fixtures", {}),
        registered_controls=locators_registry.get("controls", {}),
        purchase_data=load_yaml("purchase.yaml"),
        enums=steps_registry.get("enums", {}),
        existing_tests=existing[-4000:],
        fixture=config["fixture"],
        stream=stream,
    )

    # 校验：FinalAgent 调用了注册表里没有的方法 -> 回头让 StepAgent 补 -> 重新生成
    errors = validate(
                test_code,
                set(registered_steps),
                registered_steps,
                data_keys=set(load_yaml("purchase.yaml").keys()),
                fixtures=steps_registry.get("fixtures", {}),
                existing_code=existing,
                case_ids=[case["id"] for case in cases],
            )
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
            # 重新生成测试代码，传入错误信息让 FinalAgent 修复
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
                previous_code=test_code,
                previous_errors=errors,
            )
            errors = validate(
                test_code,
                set(registered_steps),
                registered_steps,
                data_keys=set(load_yaml("purchase.yaml").keys()),
                fixtures=steps_registry.get("fixtures", {}),
                existing_code=existing,
                case_ids=[case["id"] for case in cases],
            )
            log.info("[校验] 补齐后：%s", errors or "通过")
        elif apply and errors:
            # 没有缺失方法，但有其他错误（如数据引用、断言格式），让 FinalAgent 修复
            log.info("[校验] 调用 FinalAgent 修复 %s 个错误", len(errors))
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
                previous_code=test_code,
                previous_errors=errors,
            )
            errors = validate(
                test_code,
                set(registered_steps),
                registered_steps,
                data_keys=set(load_yaml("purchase.yaml").keys()),
                fixtures=steps_registry.get("fixtures", {}),
                existing_code=existing,
                case_ids=[case["id"] for case in cases],
            )
            log.info("[校验] 修复后：%s", errors or "通过")
        elif missing:
            log.info("[dry-run] 缺失 step：%s", missing)
    if apply and not errors:
        append_code(test_path, test_code)
        test_written = True
    elif errors:
        log.error("[校验] 仍有错误，拒绝写入 %s：%s", test_path, errors)
        test_written = False
    else:
        test_written = False
        if stream_enabled:
            log.info("[dry-run] FinalAgent 输出已在上方流式展示，未写入文件")
        else:
            log.info("[dry-run] FinalAgent 输出预览：\n%s", test_code[:1000])

    result = {
        "sheet": sheet,
        "cases": len(cases),
        "missing_steps": sum(len(missing) for _, missing in missing_by_case),
        "page_written": page_written,
        "test_written": test_written,
        "errors": errors,
    }
    if run_dir:
        result["run_dir"] = str(run_dir)
        result["page_copy"] = str(page_path)
        result["test_copy"] = str(test_path)
    return result


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
    parser.add_argument(
        "--label",
        default="",
        help="自定义副本目录名（默认按 sheet_时间戳命名）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略已有 case id 去重（默认同一用例不重复生成）",
    )
    args = parser.parse_args(argv)

    result = run(
        args.sheet,
        xlsx=Path(args.xlsx),
        apply=args.apply,
        limit=args.limit,
        stream=not args.no_stream,
        label=args.label,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
