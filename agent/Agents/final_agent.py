"""最终编排Agent：生成最终测试用例脚本到 testcases/。

数据来源
--------
- page 类中已有的测试步骤（step）
- ``step_agent`` 生成的测试步骤

输出
----
只输出**要追加的测试方法代码**，由 ``pipeline`` 写入对应 ``test_*.py``。
"""
from __future__ import annotations

from agent.Agents.Base_Agent import BaseAgent, log


class FinalAgent(BaseAgent):
    """最终编排 Agent：把 page 方法拼成 pytest 用例。"""

    name = "最终编排Agent"
    instructions = (
        "你是 pytest 测试代码生成器。根据 Excel 用例、page 类方法、现有测试文件风格，"
        "生成新的测试方法。只输出要追加的方法代码，不要输出整文件，不要解释。"
        "方法必须：4 空格缩进、@allure.story + @pytest.mark.regression + @pytest.mark.web、"
        "参数用给定 fixture、每条用例至少一个带中文失败信息的 assert。"
    )

    def generate_tests(
        self,
        *,
        sheet: str,
        page_class: str,
        page_source: str,
        cases: list[dict],
        existing_tests: str,
        fixture: str,
    ) -> str:
        case_text = "\n".join(
            f"- {case['id']} | {case['purpose']} | 步骤：{'; '.join(case['steps'])} | "
            f"预期：{case['expected']} | 断言：{case['assertions']}"
            for case in cases
        )

        prompt = f"""测试工作表：{sheet}
页面类：{page_class}
fixture：{fixture}

page 类可用方法：
```python
{page_source}
```

现有测试文件（参考风格，不要重复已有用例）：
```python
{existing_tests}
```

待生成用例：
{case_text}

要求：
1. 每条用例一个 test_ 方法，命名 test_<模块>_<编号>_<英文描述>；
2. 装饰器顺序：@allure.story("<编号> <简短描述>")、@pytest.mark.regression、@pytest.mark.web；
3. 方法参数用 fixture：{fixture}: {page_class}；
4. 只调用 page 类已有方法，不要自己编方法名；
5. 每条用例至少一个 assert，带中文失败信息；
6. 只输出新增的方法代码（4 空格缩进，放在类内），不要输出类定义和 import。
"""
        log.info("[最终编排Agent] 为 %s 条用例生成测试方法", len(cases))
        return self.run(prompt)
