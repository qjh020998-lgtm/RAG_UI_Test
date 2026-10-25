"""步骤生成agent：把测试用例的测试步骤落到 page 类中。

数据来源
--------
- ``data/step.yaml``：已注册的 step（方法名 + 中文描述）
- ``data/locator.yaml``：已注册的 locator
- 控件缓存（``component_agent`` 生成）

不再把整个 page 类源码塞进 prompt，只给"方法名 -> 中文描述"的注册表。
"""
from __future__ import annotations

from agent.Agents.Base_Agent import BaseAgent, log


class StepAgent(BaseAgent):
    """步骤生成 Agent：为 page 类补齐缺失的测试步骤方法。"""

    name = "步骤生成Agent"
    instructions = (
        "你是 ERP UI 自动化测试的 Page Object 方法生成器。"
        "输入包含：已注册的 step 清单（方法名 + 中文描述）、已注册的 locator、"
        "控件缓存、需要新增的测试步骤。"
        "只输出要新增的 Python 方法代码，不要输出整文件，不要解释。"
    )

    def generate_methods(
        self,
        page_class: str,
        steps: list[str],
        controls: list[dict],
        *,
        registered_steps: dict[str, dict],
        registered_locators: dict[str, dict],
        module: str = "",
        stream: bool | None = None,
    ) -> str:
        """生成缺失步骤的方法代码；``stream=None`` 时跟随 ``AGENT_STREAM``。"""
        step_lines = "\n".join(
            f"- {name}({', '.join(meta.get('args', []))}) "
            f"[{meta.get('scope', '?')}/{meta.get('module', 'common')}]: {meta.get('step', '')}"
            for name, meta in registered_steps.items()
        )
        locator_lines = "\n".join(
            f"- {name}: {meta.get('value', '')}"
            for name, meta in registered_locators.items()
            if meta.get("type") in ("css", "url")
        )
        control_lines = "\n".join(
            f"- {item['name']}（{item['role']}）"
            + (f" -> {item['locator']}" if item.get("locator") else "")
            for item in controls[:100]
        )
        task_lines = "\n".join(f"{index + 1}. {step}" for index, step in enumerate(steps))

        prompt = f"""页面类：{page_class}
业务模块：{module}

已注册的 step（方法名(参数) [scope/module]: 中文描述）：
{step_lines}

已注册的 locator（常量名: 值）：
{locator_lines}

可用控件（来自缓存）：
{control_lines}

需要新增的测试步骤：
{task_lines}

要求：
1. 先逐条判断：已注册 step 里是否已有覆盖该步骤的；有就输出一行 `# 已存在: <方法名> <- <步骤>`，不要生成新方法；
1.1 **scope 约束**：`[list]` 方法只能在列表页调用；`[create_modal]` 方法必须在弹窗/表单内调用。
    `click_row_action` 只能覆盖「列表行上直接可见的按钮」，**表单界面里的按钮（如"转采购退货"）必须单独生成方法**，不要用 click_row_action 硬凑；
2. 再判断：已注册 locator 里是否有可用定位；有就直接用，不要自己编；
3. 只为确实缺失的步骤生成方法；
4. **所有代码顶格输出（不要加任何缩进）**，pipeline 会统一缩进到类内；
5. 方法名英文 snake_case，带 @allure.step("中文描述")；
6. 定位优先用已注册的 locator 常量，其次 get_by_role / get_by_label / get_by_text；
7. **如果步骤需要的控件既不在「可用控件」清单、也没有可复用 locator，输出一行
   `# 需补充控件: <控件名>`，不要自己编 locator**；
8. 不要调用未注册的辅助方法（例如 self._first_visible）；
9. 每个方法只做一件事，参数用字符串/整数。
"""
        log.info("[步骤生成Agent] 生成 %s 个步骤的方法", len(steps))
        return self.run(prompt, stream=stream)
