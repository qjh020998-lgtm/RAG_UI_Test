"""最终编排Agent：按「setup → list 步骤 → 弹窗步骤 → 断言」四段生成 pytest 用例。

数据来源
--------
- ``data/step.yaml``：
  - ``kind=step`` + ``scope=list``：列表页操作
  - ``kind=step`` + ``scope=create_modal``：弹窗内操作
  - ``kind=assert``：断言
  - ``fixtures``：setup（全部标注 setup）
- ``data/locator.yaml`` 的 ``controls``：每个页面 × 状态的控件清单
- ``data/purchase.yaml``：数据锚点
"""
from __future__ import annotations

import json

from agent.Agents.Base_Agent import BaseAgent, log

USEFUL_FIXTURES = {
    "purchase_data",
    "purchase_apply_page",
    "purchase_page",
    "purchase_in_page",
    "purchase_back_page",
    "retail_out_page",
    "retail_back_page",
}

# controls 只放这些页面的弹窗控件，避免 prompt 过大
CONTROL_PAGES = ("采购订单", "采购退货", "采购入库", "请购单")
MAX_CONTROLS_PER_STATE = 25


def _group(registered_steps: dict, kind: str, scope: str) -> str:
    """按 kind + scope 分组，输出「方法名(参数) -> 返回 [模块]: 中文」。"""
    lines = []
    for name, meta in registered_steps.items():
        if meta.get("kind") != kind or meta.get("scope") != scope:
            continue
        args = ", ".join(meta.get("args", []))
        returns = meta.get("returns") or "None"
        module = meta.get("module", "common")
        lines.append(f"- {name}({args}) -> {returns} [{module}]: {meta.get('step', '')}")
    return "\n".join(lines)


def _control_lines(registered_controls: dict) -> str:
    """只取相关页面的 create_modal 控件，截断到 25 个。"""
    lines = []
    for page in CONTROL_PAGES:
        states = registered_controls.get(page, {})
        for state in ("create_modal", "list"):
            items = states.get(state, [])[:MAX_CONTROLS_PER_STATE]
            if not items:
                continue
            names = "、".join(f"{item['name']}({item['role']})" for item in items)
            lines.append(f"- {page}/{state}：{names}")
    return "\n".join(lines)


class FinalAgent(BaseAgent):
    """最终编排 Agent：按 scope 分四段生成 pytest 用例。"""

    name = "最终编排Agent"
    instructions = (
        "你是 pytest 测试代码生成器。根据 Excel 用例、已注册的 page step（带 scope/module）、"
        "控件清单、数据锚点，生成新的测试方法。"
        "只输出要追加的方法代码，不要输出整文件，不要解释。"
    )

    def generate_tests(
        self,
        *,
        sheet: str,
        page_class: str,
        cases: list[dict],
        registered_steps: dict[str, dict],
        registered_fixtures: dict[str, dict],
        registered_controls: dict[str, dict],
        purchase_data: dict,
        existing_tests: str,
        fixture: str,
        stream: bool | None = None,
        previous_code: str = "",
        previous_errors: list[str] | None = None,
    ) -> str:
        """生成 pytest 测试方法；``stream=None`` 时跟随 ``AGENT_STREAM``。"""
        list_steps = _group(registered_steps, "step", "list")
        modal_steps = _group(registered_steps, "step", "create_modal")
        list_asserts = _group(registered_steps, "assert", "list")
        modal_asserts = _group(registered_steps, "assert", "create_modal")
        fixture_lines = "\n".join(
            f"- {name}（{meta.get('scope', 'function')} 级，返回 {meta.get('returns') or 'None'}）"
            for name, meta in registered_fixtures.items()
            if name in USEFUL_FIXTURES
        )
        control_lines = _control_lines(registered_controls)
        data_lines = json.dumps(purchase_data, ensure_ascii=False, indent=2)

        case_text = "\n".join(
            f"- {case['id']} | {case['purpose']} | 步骤：{'; '.join(case['steps'])} | "
            f"预期：{case['expected']} | 断言：{case['assertions']}"
            for case in cases
        )

        retry_hint = ""
        if previous_code and previous_errors:
            error_lines = "\n".join(f"- {error}" for error in previous_errors)
            retry_hint = f"""
## 上一次生成的代码（有问题，必须修复）
```python
{previous_code}
```

## 校验器报的错误（逐条修复后重新生成）
{error_lines}
"""

        prompt = f"""测试工作表：{sheet}
页面类：{page_class}
测试 fixture：{fixture}

## 数据锚点（禁止编造，只能用这些值）
```json
{data_lines}
```
使用方式：`PURCHASE_DATA["supplier"]`

## setup（fixture，可直接用）
{fixture_lines}

## list 步骤（必须先在列表页，即 open_xxx() 之后）
{list_steps}

## create_modal 步骤（必须先 open_add_modal() 打开弹窗）
{modal_steps}

## 控件清单（弹窗里实际存在的控件，只能按这些名字定位）
{control_lines}

## list 断言（列表页用）
{list_asserts}

## create_modal 断言（弹窗内用）
{modal_asserts}

现有测试文件（参考风格，不要重复已有用例）：
```python
{existing_tests}
```

待生成用例：
{case_text}

{retry_hint}
## 硬性规则
1. 方法签名必须是 `def test_xxx(self, {fixture}: {page_class}) -> None`，第一个参数是 self；
2. 装饰器顺序：@allure.story("<编号> <简短描述>")、@pytest.mark.regression、@pytest.mark.web；
3. **需要前置单据时调 create_xxx(...) 自建，不要编造单据编号**；
4. **create_xxx 会切换页面，调用后必须重新 open_xxx() 导航回目标页**；
5. **list 步骤只能在 open_xxx() 之后调用；create_modal 步骤只能在 open_add_modal() 之后调用**；
6. **禁止在列表页调用 select_xxx / fill_xxx / choose_xxx**（这些是弹窗内方法）；
7. **禁止硬编码"测试供应商""测试仓库"等不存在的值，一律用 PURCHASE_DATA["..."]**；
8. 每条用例至少一个 assert，带中文失败信息；
9. **所有代码顶格输出（不要加任何缩进）**，pipeline 会统一缩进到类内；不要输出类定义和 import；
10. **`transfer_to_return(number)` 会自动打开退货单弹窗，调用后不要再 `open_add_modal()`**；
11. **断言提示文本时，优先用 Excel「预期结果」里的关键词；如果关键词太具体（如"不能大于可退数量"），
    改用更宽的关键词（如"数量"），并用 `assert_modal_visible()` 作为"被拦截"的主证据**。
"""
        log.info("[最终编排Agent] 为 %s 条用例生成测试方法", len(cases))
        return self.run(prompt, stream=stream)
