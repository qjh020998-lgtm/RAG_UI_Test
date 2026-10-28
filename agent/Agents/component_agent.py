"""控件获取Agent：在页面控件缓存缺失时抓取控件并写入缓存。

调用链
------
``ComponentAgent.ensure_cache()`` -> ``fetch_page_elements`` 工具
-> ``agent.tools.page_element_search.search_page_elements``
-> ``cache/pages/{module}_{page}_{state}.json``
"""
from __future__ import annotations

from agents.decorators import tool

from agent.Agents.Base_Agent import BaseAgent, log
from agent.tools.page_element_search import search_page_elements


@tool
def fetch_page_elements(
    module: str,
    page: str,
    state: str = "list",
    url: str = "",
    storage_state: str = "",
) -> str:
    """抓取页面控件并写入缓存，返回控件数量与名称预览。

    Args:
        module: 业务模块，如 采购管理
        page: 页面名，如 采购订单
        state: 页面状态，list / create_modal / edit_modal
        url: 页面 URL；提供时用浏览器抓 A11y，否则只走 JS bundle
        storage_state: 登录快照 JSON 路径
    """
    result = search_page_elements(
        module,
        page,
        state=state or "list",
        url=url,
        use_browser=bool(url),
        storage_state=storage_state,
        refresh=True,
    )
    names = [item["name"] for item in result.get("controls", [])]
    log.info("[控件获取Agent] 抓到 %s 个控件（sources=%s）", len(names), result.get("sources"))
    return f"cached {len(names)} controls: {', '.join(names[:80])}"


DISCOVERY_PROMPT = """你是 ERP UI 自动化测试的「控件获取 Agent」。

## 目标
给定业务模块（如「采购管理」），把该模块下**每个页面**、**每个状态**的控件抓全，
写入控件缓存，供后续步骤生成 Agent 复用。

## 输入
- module：业务模块名，如「采购管理」
- pages：页面清单，每项形如 `(页面名, 列表页路径)`，如 `("采购入库", "/bill/purchase_in")`
- states：状态清单，固定为 `["list", "create_modal"]`
- storage_state：Playwright 登录快照路径，如 `artifacts/auth/xxx.json`

## 执行步骤（对每个页面重复）
1. **抓列表页**
   - 调 `fetch_page_elements(module, page, state="list", url=base_url+path, storage_state=...)`
   - 记录返回的控件数量
2. **抓新增弹窗**
   - 调 `fetch_page_elements(module, page, state="create_modal", url=base_url+path, storage_state=...)`
   - 工具内部会自动点「新增」按钮、等弹窗出现、抓 A11y 树
3. **校验**
   - `list` 状态控件数必须 ≥ 50
   - `create_modal` 状态控件数必须 ≥ 50
   - 少于阈值时记录 `error`，不要静默跳过
4. **落盘**
   - 工具会自动写 `cache/pages/{module}_{page}_{state}.json`
   - 你只需要汇总结果

## 硬性要求
1. **不调 LLM 做推理**：整个过程只用 Playwright + A11y 树解析；
2. **不重复抓**：命中缓存直接返回，只有需要重抓时才 `refresh=True`；
3. **不遗漏状态**：`list` 和 `create_modal` 都要抓；有编辑弹窗时额外加 `edit_modal`；
4. **失败要报**：任何页面抓不到控件，记录 `{page, state, error}`，继续下一个，不要中断整体；
5. **速度优先**：8 组（4 页面 × 2 状态）应在 4 分钟内完成，单组超过 60 秒要告警；
6. **icon 不是 button**：A11y 里的 `button "图标: search"` 实际是 `<i class="anticon-search">`，
   缓存必须写成 `role=icon, locator=i.anticon-search`，禁止写 `get_by_role('button', name='图标: ...')`；
7. **关联单据字段**：readonly input（采购订单“关联请购单”/采购入库“关联订单”/采购退货“关联单据”）
   的操作入口是右侧 `i.anticon-search`，不是输入框本身；缓存要保留 label + 图标。

## 输出格式
```json
{
  "module": "采购管理",
  "captured_at": "2026-10-25T14:18:00",
  "results": [
    {"page": "请购单", "state": "list", "controls": 196, "error": null},
    {"page": "请购单", "state": "create_modal", "controls": 169, "error": null}
  ],
  "total_controls": 1551,
  "elapsed_seconds": 218
}
```

## 验收标准
- 4 个页面 × 2 个状态 = 8 组，每组 `controls ≥ 50`
- 总耗时 ≤ 4 分钟
- 任一组失败时，`error` 字段有具体原因（如「页面不在 chunk 映射里」）
"""


class ComponentAgent(BaseAgent):
    """控件获取 Agent：缓存 miss 时抓取页面控件。"""

    name = "控件获取Agent"
    instructions = (
        "你负责页面控件获取。调用 fetch_page_elements 工具，参数从用户消息中提取："
        "module、page、state、url、storage_state。抓取完成后用一句话汇报控件数量。"
    )

    def __init__(self):
        super().__init__(tools=[fetch_page_elements])

    def ensure_cache(
        self,
        module: str,
        page: str,
        *,
        state: str = "create_modal",
        url: str = "",
        storage_state: str = "",
        stream: bool | None = None,
    ) -> str:
        """让 Agent 调工具抓取控件，返回 Agent 的最终文本。"""
        prompt = (
            f"module={module}\npage={page}\nstate={state}\n"
            f"url={url}\nstorage_state={storage_state}\n"
            "请调用工具抓取控件。"
        )
        return self.run(prompt, stream=stream)
