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
    ) -> str:
        """让 Agent 调工具抓取控件，返回 Agent 的最终文本。"""
        prompt = (
            f"module={module}\npage={page}\nstate={state}\n"
            f"url={url}\nstorage_state={storage_state}\n"
            "请调用工具抓取控件。"
        )
        return self.run(prompt)
