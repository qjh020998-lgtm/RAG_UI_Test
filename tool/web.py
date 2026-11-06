"""网页搜索、抓取和 ERP 页面 DOM 采集工具（LangChain 版）。

采集工具只有一个入口：``capture_page``，支持：

- list：列表页；
- add：新增弹窗 + 明细表 + 关联弹窗 + 商品弹窗 + 下拉选项；
- all：列表 + 新增，一次完成。

跳过扫码、导入明细、历史单据、附件弹窗。
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from langchain.tools import tool
from pydantic import BaseModel, Field

from tool.context import DEFAULT_BASE_URL, ToolContext, get_context
import logging

DEFAULT_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"

logger = logging.getLogger(__name__)
# ----------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------


def _config(ctx: ToolContext, key: str, default: Any = None) -> Any:
    return ctx.config.get(key, default)


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", name).strip("_") or "page"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def _texts(locator: Any) -> list[str]:
    values = await locator.all_inner_texts()
    return [cleaned for value in values if (cleaned := _clean(value))]


async def _attrs(locator: Any) -> dict[str, Any]:
    if not await locator.count():
        return {}
    return await locator.evaluate(
        """el => ({
          tag: el.tagName,
          id: el.id,
          cls: el.className,
          placeholder: el.getAttribute('placeholder'),
          ariaLabel: el.getAttribute('aria-label'),
          role: el.getAttribute('role'),
          readOnly: el.readOnly,
          value: el.value ?? null,
        })"""
    )


async def _kill_guide(page: Any) -> None:
    try:
        await page.evaluate(
            "() => document.querySelectorAll('.introjs-overlay,.introjs-tooltip,"
            ".introjs-helperLayer,.introjs-disableInteraction').forEach(n => n.remove())"
        )
    except Exception:
        pass


async def _goto(page: Any, url: str, *, retries: int = 2) -> None:
    """ERP 偶发连接超时，导航失败时短暂等待后重试。"""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                await page.wait_for_timeout(1000)
    assert last_error is not None
    raise last_error


async def _close_top_modal(page: Any) -> None:
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
    except Exception:
        pass


async def _close_add_modal(page: Any) -> None:
    try:
        close = page.locator(".ant-modal:visible .ant-modal-close").last
        if await close.count():
            await close.click()
        else:
            await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
        confirm = page.locator(".ant-modal-confirm:visible")
        if await confirm.count():
            await confirm.last.locator(".ant-btn-primary").last.click()
            await page.wait_for_timeout(300)
    except Exception:
        pass


# ----------------------------------------------------------------------
# 浏览器会话
# ----------------------------------------------------------------------


class LoginError(RuntimeError):
    """登录态失效且自动登录失败。"""


class BrowserSession:
    """一个模块的页面会话。

    可以自己启动浏览器；也可以通过 BrowserPool 共享一个浏览器进程，
    只创建独立 context/page。
    """

    LOGIN_PATH = "/user/login"
    USERNAME = "#loginName:visible, input[placeholder='请输入用户名']:visible"
    PASSWORD = "#password:visible, input[placeholder='请输入密码']:visible"
    LOGIN_BTN = (
        ".login-button:visible, "
        "button:has-text('登录'):visible, "
        "button:has-text('登 录'):visible"
    )

    def __init__(self, ctx: ToolContext, *, runtime: BrowserPool | None = None) -> None:
        self.ctx = ctx
        self._runtime = runtime
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self.page: Any = None

    @property
    def base_url(self) -> str:
        return (
            self.ctx.base_url
            or str(_config(self.ctx, "base_url", ""))
            or os.getenv("BASE_URL", "")
            or DEFAULT_BASE_URL
        ).rstrip("/")

    def _storage_state(self) -> str | None:
        storage_state = self.ctx.storage_state or str(_config(self.ctx, "storage_state", ""))
        if not storage_state:
            return None
        path = Path(storage_state).expanduser()
        if not path.is_absolute():
            path = self.ctx.workspace / path
        return str(path) if path.exists() else None

    async def _new_context(self, browser: Any) -> Any:
        return await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            storage_state=self._storage_state(),
        )

    async def ensure_page(self) -> Any:
        if self.page is not None and not self.page.is_closed():
            return self.page

        if self._runtime is not None:
            browser = await self._runtime.ensure_browser()
            self._context = await self._new_context(browser)
            self.page = await self._context.new_page()
            await self._ensure_login()
            return self.page

        
        self._playwright = await async_playwright().start()
        headless = bool(_config(self.ctx, "headless", True))
        self._browser = await self._playwright.chromium.launch(headless=headless)
        self._context = await self._new_context(self._browser)
        self.page = await self._context.new_page()
        await self._ensure_login()
        return self.page

    async def _login_required(self, page: Any) -> bool:
        """页面是否要求登录：URL 在登录页，或页面上有可见登录表单。"""
        if self.LOGIN_PATH in page.url:
            return True
        try:
            return await page.locator(self.USERNAME).first.is_visible(timeout=500)
        except Exception:
            return False

    async def login(self, page: Any) -> None:
        """页面登录或弹窗登录；失败抛 LoginError。"""
        username = str(_config(self.ctx, "username", "") or os.getenv("ERP_USERNAME", ""))
        password = str(_config(self.ctx, "password", "") or os.getenv("ERP_PASSWORD", ""))
        if not username or not password:
            raise LoginError(
                "缺少 username/password；请在 ToolContext.config 或 "
                "ERP_USERNAME/ERP_PASSWORD 中提供"
            )

        root = page
        if self.LOGIN_PATH not in page.url:
            modal = page.locator(".ant-modal:visible").filter(has=page.locator(self.USERNAME))
            if await modal.count():
                root = modal.first
            else:
                await _goto(page, f"{self.base_url}{self.LOGIN_PATH}")

        try:
            await root.locator(self.USERNAME).first.fill(username)
            await root.locator(self.PASSWORD).first.fill(password)
            await root.locator(self.LOGIN_BTN).first.click()
        except Exception as exc:  # noqa: BLE001
            raise LoginError(f"登录操作失败：{type(exc).__name__}: {exc}") from exc

        for _ in range(60):
            if not await self._login_required(page):
                break
            await page.wait_for_timeout(500)
        else:
            raise LoginError("登录失败：仍停留在登录页或登录弹窗")
        await page.wait_for_timeout(500)

    async def _ensure_login(self) -> None:
        page = self.page
        await _goto(page, f"{self.base_url}/dashboard/analysis")
        await page.wait_for_timeout(1200)
        if await self._login_required(page):
            await self.login(page)

    async def goto(self, url: str) -> Any:
        """导航到业务页，登录态失效时自动登录并重试一次。"""
        page = await self.ensure_page()
        await _goto(page, url)
        await page.wait_for_timeout(500)
        if await self._login_required(page):
            await self.login(page)
            await _goto(page, url)
            if await self._login_required(page):
                raise LoginError("登录后访问业务页仍被要求登录")
        return page

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._runtime is None:
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:
                    pass
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
            self._browser = None
            self._playwright = None
        self.page = None
        self._context = None


class BrowserPool:
    """一个 Chromium 进程，多个隔离 context/page。"""

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self._playwright: Any = None
        self.browser: Any = None
        self._sessions: list[BrowserSession] = []
        self._lock = asyncio.Lock()

    async def ensure_browser(self) -> Any:
        if self.browser is not None:
            return self.browser
        async with self._lock:
            if self.browser is not None:
                return self.browser
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            headless = bool(_config(self.ctx, "headless", True))
            self.browser = await self._playwright.chromium.launch(headless=headless)
            return self.browser

    async def new_session(self, ctx: ToolContext) -> BrowserSession:
        await self.ensure_browser()
        session = BrowserSession(ctx, runtime=self)
        await session.ensure_page()
        self._sessions.append(session)
        return session

    async def close(self) -> None:
        for session in self._sessions:
            await session.close()
        self._sessions.clear()
        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


def _session(ctx: ToolContext) -> BrowserSession:
    if isinstance(ctx.browser, BrowserSession):
        return ctx.browser
    session = BrowserSession(ctx)
    ctx.browser = session
    return session


# ----------------------------------------------------------------------
# 可复用采集片段
# ----------------------------------------------------------------------


async def _form_items(root: Any) -> list[dict[str, Any]]:
    """采集表单字段：标签、必填、控件属性。列表搜索和新增弹窗共用。"""
    items = root.locator(".ant-form-item:visible")
    result: list[dict[str, Any]] = []
    for index in range(await items.count()):
        item = items.nth(index)
        label = item.locator("label").first
        label_text = _clean(await label.inner_text()) if await label.count() else ""
        if not label_text:
            continue
        controls = []
        for kind in ("input", "textarea", ".ant-select", ".ant-calendar-picker"):
            loc = item.locator(kind)
            for control_index in range(min(await loc.count(), 3)):
                controls.append({
                    "kind": kind,
                    "attrs": await _attrs(loc.nth(control_index)),
                })
        result.append({
            "label": label_text,
            "required": "ant-form-item-required" in ((await item.get_attribute("class")) or ""),
            "controls": controls,
        })
    return result


async def _table_snapshot(root: Any, *, max_rows: int, include_html: bool) -> dict[str, Any]:
    """采集一个表格：表头、行、单元格、选择状态、HTML 片段。"""
    table = root.locator(".ant-table:visible").first
    if not await table.count():
        table = root.locator("table:visible").first

    custom = False
    if not await table.count():
        table = root.locator(".thead").first
        custom = await table.count() > 0
    if not custom and not await table.count():
        return {}

    headers = []
    row_locator = None
    if custom:
        headers = await _texts(table.locator(".td"))
        row_locator = root.locator(".tbody .tr")
    else:
        headers = await _texts(table.locator("thead th"))
        if not headers:
            headers = await _texts(table.locator("thead td"))
        row_locator = table.locator("tbody tr")

    row_count = await row_locator.count()
    rows = []
    for index in range(min(row_count, max_rows)):
        row = row_locator.nth(index)
        cells = [_clean(value) for value in await row.locator(".td, td").all_inner_texts()]
        checked = await row.locator("input:checked").count()
        radios = await row.locator("input[type=radio]").count()
        checkboxes = await row.locator("input[type=checkbox]").count()
        selection_type = "radio" if radios else ("checkbox" if checkboxes else "")
        rows.append({
            "cells": cells,
            "text": _clean(await row.inner_text()),
            "selected": checked > 0,
            "selection_type": selection_type,
            "html": (await row.inner_html())[:2000] if include_html and index == 0 else "",
        })

    selection_type = next((row["selection_type"] for row in rows if row["selection_type"]), "")
    table_html = ""
    if include_html:
        if custom:
            table_html = (await table.inner_html())[:3000]
        else:
            table_html = (await table.inner_html())[:5000]
    return {
        "headers": headers,
        "rows": rows,
        "row_count": row_count,
        "selection_type": selection_type,
        "selected_count": sum(1 for row in rows if row["selected"]),
        "html": table_html,
    }


async def _select_options(page: Any, root: Any, *, max_options: int = 50) -> dict[str, list[str]]:
    """展开表单里的 select，采集选项；按标签分组。"""
    result: dict[str, list[str]] = {}
    items = root.locator(".ant-form-item:visible")
    for index in range(min(await items.count(), 20)):
        item = items.nth(index)
        label = item.locator("label").first
        if not await label.count():
            continue
        label_text = _clean(await label.inner_text())
        select = item.locator(".ant-select").first
        if not label_text or not await select.count():
            continue
        try:
            await select.click()
            await page.wait_for_timeout(350)
            options = page.locator(".ant-select-dropdown:visible li")
            values = await _texts(options)
            result[label_text] = values[:max_options]
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
        except Exception:
            await page.keyboard.press("Escape")
    return result


async def _row_controls(root: Any) -> dict[str, Any]:
    """采集第一条明细行的控件，表单场景复用。"""
    row = root.locator(".tbody .tr").first
    if not await row.count():
        row = root.locator("tbody tr").first
    if not await row.count():
        return {"text": "", "inputs": [], "selects": [], "icons": []}
    return {
        "text": _clean(await row.inner_text())[:300],
        "inputs": [
            await _attrs(row.locator("input").nth(i))
            for i in range(min(await row.locator("input").count(), 8))
        ],
        "selects": [
            await _attrs(row.locator(".ant-select").nth(i))
            for i in range(min(await row.locator(".ant-select").count(), 3))
        ],
        "icons": [
            await _attrs(row.locator("i.anticon-search").nth(i))
            for i in range(min(await row.locator("i.anticon-search").count(), 3))
        ],
    }


async def _modal_snapshot(
    page: Any,
    root: Any,
    *,
    max_rows: int,
    include_html: bool,
) -> dict[str, Any]:
    """通用弹窗采集：标题、字段、表格、按钮、HTML。"""
    title = (
        _clean(await root.locator(".ant-modal-title").inner_text())
        if await root.locator(".ant-modal-title").count()
        else ""
    )
    fields = await _form_items(root)
    search_placeholder = ""
    for field in fields:
        for control in field["controls"]:
            if control["kind"] == "input" and control["attrs"].get("placeholder"):
                search_placeholder = control["attrs"]["placeholder"]
                break
        if search_placeholder:
            break

    table = await _table_snapshot(root, max_rows=max_rows, include_html=include_html)
    return {
        "title": title,
        "search_placeholder": search_placeholder,
        "fields": fields,
        "table": table,
        "table_headers": table.get("headers", []),
        "rows": table.get("rows", []),
        "selection_type": table.get("selection_type", ""),
        "selected_count": table.get("selected_count", 0),
        "buttons": await _texts(root.locator("button:visible")),
        "html": (await root.inner_html())[:6000] if include_html else "",
    }


# ----------------------------------------------------------------------
# 页面 / 弹窗操作
# ----------------------------------------------------------------------


async def _open_add(page: Any) -> Any:
    buttons = page.locator("button:visible")
    for index in range(await buttons.count()):
        button = buttons.nth(index)
        try:
            text = (await button.inner_text()).replace(" ", "").strip()
        except Exception:
            continue
        if text == "新增":
            await button.click()
            await page.wait_for_timeout(1200)
            await _kill_guide(page)
            break
    else:
        raise RuntimeError("未找到新增按钮")

    if await page.locator(".ant-modal:visible").count():
        return page.locator(".ant-modal:visible").last
    return page.locator("body")


async def _open_link_modal(page: Any, root: Any) -> Any | None:
    item = root.locator(".ant-form-item:visible").filter(has_text="关联").first
    icon = item.locator("i.anticon-search").first
    if not await item.count() or not await icon.count():
        return None
    await icon.click()
    await page.wait_for_timeout(1000)
    if not await page.locator(".ant-modal:visible").count():
        return None
    return page.locator(".ant-modal:visible").last


async def _open_product_modal(page: Any, root: Any) -> Any | None:
    row = root.locator(".tbody .tr").first
    if not await row.count():
        row = root.locator("tbody tr").first
    icon = row.locator("i.anticon-search").first
    if not await icon.count():
        return None
    await icon.click()
    await page.wait_for_timeout(1000)
    if not await page.locator(".ant-modal:visible").count():
        return None
    return page.locator(".ant-modal:visible").last


# ----------------------------------------------------------------------
# list / add 采集
# ----------------------------------------------------------------------


async def _capture_list(
    session: BrowserSession,
    module: str,
    path: str,
    *,
    max_rows: int,
    include_html: bool,
) -> dict[str, Any]:
    page = await session.goto(f"{session.base_url}{path}")
    await page.wait_for_timeout(1200)
    await _kill_guide(page)

    # 列表页偶发首屏慢，等表头出现再采集。
    for attempt in range(3):
        try:
            await page.wait_for_selector(".ant-table:visible thead th", timeout=8000)
            break
        except Exception:
            if attempt < 2:
                await page.wait_for_timeout(1000)
    await page.wait_for_timeout(500)

    table = await _table_snapshot(page.locator("body"), max_rows=max_rows, include_html=include_html)
    record: dict[str, Any] = {
        "module": module,
        "path": path,
        "url": page.url,
        "final_url": page.url,
        "title": await page.title(),
        "buttons": await _texts(page.locator("button:visible")),
        "links": [],
        "table": table,
        "table_headers": table.get("headers", []),
        "row_count": table.get("row_count", 0),
        "search_items": await _form_items(page.locator("body")),
        "options": await _select_options(page, page.locator("body")),
        "html": (await page.content())[:6000] if include_html else "",
    }
    links = page.locator("a[href]:visible")
    for index in range(min(await links.count(), 60)):
        link = links.nth(index)
        text = _clean(await link.inner_text())
        if text:
            record["links"].append({
                "text": text,
                "href": await link.get_attribute("href"),
            })

    shot = session.ctx.artifacts / "screenshots" / f"{_safe_name(module)}_list.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(shot), full_page=True)
    record["screenshot"] = str(shot)

    json_path = session.ctx.artifacts / "dom" / f"{_safe_name(module)}_list.json"
    _write_json(json_path, record)
    record["json"] = str(json_path)
    return record


async def _capture_add(
    session: BrowserSession,
    module: str,
    path: str,
    *,
    max_rows: int,
    include_html: bool,
) -> dict[str, Any]:
    page = await session.goto(f"{session.base_url}{path}")
    await page.wait_for_timeout(1200)
    await _kill_guide(page)

    root = await _open_add(page)
    modal = await _modal_snapshot(page, root, max_rows=max_rows, include_html=include_html)
    options = await _select_options(page, root)

    link_modal = None
    link_root = await _open_link_modal(page, root)
    if link_root is not None:
        link_modal = await _modal_snapshot(page, link_root, max_rows=max_rows, include_html=include_html)
        await _close_top_modal(page)

    product_modal = None
    product_root = await _open_product_modal(page, root)
    if product_root is not None:
        product_modal = await _modal_snapshot(page, product_root, max_rows=max_rows, include_html=include_html)
        await _close_top_modal(page)

    record: dict[str, Any] = {
        "module": module,
        "path": path,
        "modal_count": await page.locator(".ant-modal:visible").count(),
        "modal": modal,
        "fields": modal["fields"],
        "detail_table": modal["table"],
        "detail_headers": modal["table_headers"],
        "detail_row": await _row_controls(root),
        "thead_html": modal["table"].get("html", ""),
        "detail_row_html": (
            modal["table"].get("rows", [{}])[0].get("html", "")
            if modal["table"].get("rows")
            else ""
        ),
        "buttons": modal["buttons"],
        "options": options,
        "link_modal": link_modal,
        "product_modal": product_modal,
    }

    shot = session.ctx.artifacts / "screenshots" / f"{_safe_name(module)}_add.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(shot), full_page=True)
    record["screenshot"] = str(shot)

    await _close_add_modal(page)
    json_path = session.ctx.artifacts / "dom" / f"{_safe_name(module)}_add.json"
    _write_json(json_path, record)
    record["json"] = str(json_path)
    return record


# ----------------------------------------------------------------------
# compact facts
# ----------------------------------------------------------------------


def _compact_facts(data: dict[str, Any]) -> dict[str, Any]:
    """把完整 capture 结果投影成适合模型读取的小 facts。"""
    list_data = data.get("list") or {}
    add_data = data.get("add") or {}

    def controls(item: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "kind": control.get("kind"),
                "placeholder": (control.get("attrs") or {}).get("placeholder"),
            }
            for control in item.get("controls", [])
        ]

    def modal(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not value:
            return None
        rows = value.get("rows") or (value.get("table") or {}).get("rows") or []
        return {
            "title": value.get("title"),
            "search_placeholder": value.get("search_placeholder"),
            "table_headers": value.get("table_headers")
            or (value.get("table") or {}).get("headers", []),
            "rows": [
                {
                    "cells": row.get("cells", []),
                    "selected": row.get("selected", False),
                }
                for row in rows[:3]
            ],
            "selection_type": value.get("selection_type"),
            "selected_count": value.get("selected_count", 0),
        }

    return {
        "module": data.get("module"),
        "path": data.get("path"),
        "list": {
            "buttons": list_data.get("buttons", []),
            "table_headers": list_data.get("table_headers")
            or (list_data.get("table") or {}).get("headers", []),
            "search_items": [
                {
                    "label": item.get("label"),
                    "controls": controls(item),
                }
                for item in list_data.get("search_items", [])
            ],
            "row_count": list_data.get("row_count", 0),
            "options": list_data.get("options", {}),
        },
        "add": {
            "fields": [
                {
                    "label": field.get("label"),
                    "required": field.get("required", False),
                    "controls": controls(field),
                }
                for field in add_data.get("fields", [])
            ],
            "detail_headers": add_data.get("detail_headers", []),
            "detail_row": add_data.get("detail_row", {}),
            "buttons": add_data.get("buttons", []),
            "options": add_data.get("options", {}),
            "link_modal": modal(add_data.get("link_modal")),
            "product_modal": modal(add_data.get("product_modal")),
        },
    }


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------


class CapturePageInput(BaseModel):
    module: str = Field(description="模块名，如 采购订单")
    path: str = Field(description="列表页路径，如 /bill/purchase_order")
    scenario: Literal["list", "add", "all"] = Field(
        default="all",
        description="采集场景：list=列表，add=新增，all=列表+新增",
    )
    max_table_rows: int = Field(default=20, ge=1, le=200, description="每个表格最多采集行数")
    include_html: bool = Field(default=True, description="是否保存表格/弹窗 HTML 片段")
    compact: bool = Field(default=False, description="是否额外输出 compact facts")


@tool(args_schema=CapturePageInput)
async def capture_page(
    module: str,
    path: str,
    scenario: str = "all",
    max_table_rows: int = 20,
    include_html: bool = True,
    compact: bool = False,
) -> str:
    """采集 ERP 页面：列表、表单、表格、弹窗、关联选择表和商品选择表。

    scenario=list/add/all；compact=True 时额外输出小 facts。
    """
    logger.info(f"参数:{module}, {path}, {scenario}")
    ctx = get_context()
    session: BrowserSession | None = None
    try:
        if scenario not in ("list", "add", "all"):
            return f"错误：scenario 必须是 list/add/all，收到 {scenario!r}"

        session = _session(ctx)
        result: dict[str, Any] = {
            "module": module,
            "path": path,
            "scenario": scenario,
        }
        if scenario in ("list", "all"):
            result["list"] = await _capture_list(
                session,
                module,
                path,
                max_rows=max_table_rows,
                include_html=include_html,
            )
        if scenario in ("add", "all"):
            result["add"] = await _capture_add(
                session,
                module,
                path,
                max_rows=max_table_rows,
                include_html=include_html,
            )

        compact_path = None
        if compact:
            compact_path = (
                ctx.artifacts / "dom" / f"{_safe_name(module)}_facts_compact.json"
            )
            _write_json(compact_path, _compact_facts(result))

        summary_path = ctx.artifacts / "dom" / f"{_safe_name(module)}_capture.json"
        _write_json(summary_path, result)
        return json.dumps({
            "module": module,
            "path": path,
            "scenario": scenario,
            "summary": str(summary_path),
            "compact": str(compact_path) if compact_path else None,
            "list": result.get("list", {}).get("json"),
            "add": result.get("add", {}).get("json"),
            "fields": len(result.get("add", {}).get("fields", [])),
            "detail_headers": len(result.get("add", {}).get("detail_headers", [])),
            "link_rows": len((result.get("add", {}).get("link_modal") or {}).get("rows", [])),
            "product_rows": len((result.get("add", {}).get("product_modal") or {}).get("rows", [])),
            "options": list((result.get("add", {}).get("options") or {}).keys()),
        }, ensure_ascii=False)
    except LoginError as exc:
        if session is not None:
            await session.close()
        ctx.browser = None
        return f"错误：登录失败：{exc}"
    except Exception as exc:  # noqa: BLE001
        return f"错误：{type(exc).__name__}: {exc}"


class WebSearchInput(BaseModel):
    query: str = Field(description="搜索关键词")
    max_results: int = Field(default=5, ge=1, le=10, description="最多返回条数")


@tool(args_schema=WebSearchInput)
async def web_search(query: str, max_results: int = 5) -> str:
    """调用 DeepSeek Web Search API 搜索网页。"""
    ctx = get_context()
    try:
        import httpx

        api_key = str(_config(ctx, "deepseek_api_key", "") or os.getenv("DEEPSEEK_API_KEY", ""))
        if not api_key:
            return "错误：缺少 DEEPSEEK_API_KEY"
        url = str(
            _config(ctx, "deepseek_search_url", "")
            or os.getenv("DEEPSEEK_SEARCH_URL", "")
            or DEFAULT_SEARCH_URL
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "max_results": max_results},
            )
            response.raise_for_status()
            data = response.json()

        items = data.get("results") or data.get("data") or data.get("web_pages") or data.get("citations") or []
        if isinstance(items, dict):
            items = items.get("results") or items.get("items") or []
        results = []
        for item in items[:max_results]:
            if not isinstance(item, dict):
                continue
            results.append({
                "title": item.get("title") or item.get("name") or "",
                "url": item.get("url") or item.get("link") or "",
                "snippet": item.get("snippet") or item.get("content") or item.get("description") or "",
            })
        return json.dumps({"query": query, "results": results}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return f"错误：{type(exc).__name__}: {exc}"


class WebFetchInput(BaseModel):
    url: str = Field(description="http/https URL")
    max_chars: int = Field(default=20000, ge=1000, le=100000, description="最多返回字符数")
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = Field(
        default="GET",
        description="HTTP 方法",
    )
    headers: dict[str, str] | None = Field(default=None, description="请求头，如 Authorization")
    json_body: dict[str, Any] | None = Field(default=None, description="JSON 请求体")


@tool(args_schema=WebFetchInput)
async def web_fetch(
    url: str,
    max_chars: int = 20000,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> str:
    """抓取网页或调用 HTTP 接口（GET/POST/PUT/DELETE/PATCH），返回文本或 JSON 内容。"""
    try:
        import httpx

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "错误：只支持 http/https URL"
        request_headers = {"User-Agent": "Mozilla/5.0", **(headers or {})}
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.request(
                method.upper(),
                url,
                headers=request_headers,
                json=json_body,
            )
            response.raise_for_status()
            text = response.text
        if "html" in response.headers.get("content-type", "").lower():
            text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = html_lib.unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
        truncated = len(text) > max_chars
        return json.dumps({
            "url": str(response.url),
            "status": response.status_code,
            "content": text[:max_chars],
            "truncated": truncated,
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return f"错误：{type(exc).__name__}: {exc}"


@tool
async def close_browser() -> str:
    """关闭 DOM 采集使用的浏览器。"""
    ctx = get_context()
    session = ctx.browser
    if isinstance(session, BrowserSession):
        await session.close()
        ctx.browser = None
        return "浏览器已关闭"
    return "没有需要关闭的浏览器"


__all__ = [
    "BrowserPool",
    "BrowserSession",
    "LoginError",
    "capture_page",
    "close_browser",
    "web_fetch",
    "web_search",
]
