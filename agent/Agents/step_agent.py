"""步骤生成agent：把测试用例的测试步骤落到 page 类中。

数据来源
--------
- 已有的控件缓存（包含 ``component_agent`` 生成的控件）
- page 类源码

输出
----
只输出**要新增的方法代码**，由 ``pipeline`` 追加到 page 文件。
"""
from __future__ import annotations

from agent.Agents.Base_Agent import BaseAgent, log


class StepAgent(BaseAgent):
    """步骤生成 Agent：为 page 类补齐缺失的测试步骤方法。"""

    name = "步骤生成Agent"
    instructions = (
        "你是 ERP UI 自动化测试的 Page Object 方法生成器。"
        "输入包含 page 类源码、控件清单、缺失的测试步骤。"
        "只输出要新增的 Python 方法代码，不要输出整文件，不要解释。"
        "方法必须：4 空格缩进、带 @allure.step(\"中文描述\")、"
        "方法名英文 snake_case、定位优先 get_by_role / get_by_label / get_by_text。"
    )

    def generate_methods(
        self,
        page_class: str,
        page_source: str,
        steps: list[str],
        controls: list[dict],
        *,
        module: str = "",
    ) -> str:
        control_lines = "\n".join(
            f"- {item['name']}（{item['role']}）"
            + (f" -> {item['locator']}" if item.get("locator") else "")
            for item in controls[:120]
        )
        step_lines = "\n".join(f"{index + 1}. {step}" for index, step in enumerate(steps))

        prompt = f"""页面类：{page_class}
业务模块：{module}

page 类源码：
```python
{page_source}
```

可用控件：
{control_lines}

需要新增的测试步骤（按顺序）：
{step_lines}

要求：
1. **先逐条判断**：page 类里是否已有方法覆盖该步骤（看 @allure.step 的中文描述，或方法名语义）。
   已存在的，只输出一行注释 `# 已存在: <方法名> <- <步骤>`，**不要生成新方法**；
2. 只为确实缺失的步骤生成方法；
3. 方法名英文 snake_case，带 @allure.step("中文描述")；
4. 定位优先 get_by_role / get_by_label / get_by_text，不要写 page.locator；
5. 不要调用 page 源码里不存在的辅助方法（例如 self._first_visible）；
6. 每个方法只做一件事，参数用字符串/整数。
"""
        log.info("[步骤生成Agent] 生成 %s 个步骤的方法", len(steps))
        return self.run(prompt)
