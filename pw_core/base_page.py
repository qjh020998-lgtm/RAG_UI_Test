"""Page Object 基类：定位、操作、等待、断言、弹窗、表格。

参考 astra 的 ``core/base_page.py``：BasePage 自身提供可复用的浏览器交互
方法，Page 子类只写业务方法。定位统一走 Accessibility Tree 语义 API。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from playwright.sync_api import Locator, Page, expect

from pw_core.conf import PlaywrightConfig


class BasePage:
    """所有 Page Object 的基类。"""

    # ERP 常见组件容器的兜底选择器；优先使用 role，找不到时再退回这些组件类名。
    TOAST_SELECTOR = (
        "[role='alert'], .ant-message-notice-content, .el-message, "
        ".Toastify__toast, .ant-notification-notice"
    )
    DIALOG_SELECTOR = (
        "[role='dialog'], .ant-modal-content, .el-dialog, "
        ".ant-drawer-content, .el-drawer__body"
    )
    TABLE_SELECTOR = "table, .ant-table, .el-table"
    CONFIRM_BUTTON_PATTERN = re.compile(r"确定|确认|离开|放弃|是|保存")
    CANCEL_BUTTON_PATTERN = re.compile(r"取消|留在此页|否|关闭")

    def __init__(
        self,
        page: Page,
        base_url: str = "",
        config: PlaywrightConfig | None = None,
    ):
        self.page = page
        self.base_url = (base_url or "").rstrip("/")
        self.config = config or PlaywrightConfig()

    # ── 导航 ───────────────────────────────────────────────────────

    def navigate(self, url: str, wait_until: str = "load") -> None:
        """导航到绝对 URL。"""
        self.page.goto(url, wait_until=wait_until)

    def open(self, path: str = "", wait_until: str = "load") -> None:
        """导航到 ``base_url + path``；``path`` 也可以是绝对 URL。"""
        if path.startswith(("http://", "https://")):
            url = path
        elif self.base_url:
            url = f"{self.base_url}/{path.lstrip('/')}" if path else self.base_url
        else:
            url = path
        self.navigate(url, wait_until=wait_until)

    def get_url(self) -> str:
        """返回当前 URL。"""
        return self.page.url

    def get_title(self) -> str:
        """返回当前页面标题。"""
        return self.page.title()

    def wait_for_load(self, state: str = "networkidle") -> None:
        """等待页面加载状态。"""
        self.page.wait_for_load_state(state)

    def wait_for_page_ready(
        self,
        selector: str | Locator | None = None,
        state: str = "networkidle",
        timeout: int | None = None,
    ) -> None:
        """等待页面加载完成，可选等待某个就绪元素。"""
        self.page.wait_for_load_state(state, timeout=timeout)
        if selector is None:
            return
        target = selector if isinstance(selector, Locator) else self.page.locator(selector)
        target.first.wait_for(state="visible", timeout=timeout)

    def is_on_page(self, url_fragment: str) -> bool:
        """当前 URL 包含 ``url_fragment`` 时返回 True。"""
        return url_fragment in self.get_url()

    def reload(self) -> None:
        """刷新当前页面。"""
        self.page.reload()

    def go_back(self) -> None:
        """浏览器后退。"""
        self.page.go_back()

    def go_forward(self) -> None:
        """浏览器前进。"""
        self.page.go_forward()

    def scroll_to_bottom(self) -> None:
        """滚动到页面底部。"""
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    # ── 定位器（A11y 优先） ────────────────────────────────────────

    def by_role(
        self,
        role: str,
        name: str | None = None,
        exact: bool | None = None,
    ) -> Locator:
        """通过 ARIA role 定位。"""
        kwargs: dict = {}
        if name is not None:
            kwargs["name"] = name
        if exact is not None:
            kwargs["exact"] = exact
        return self.page.get_by_role(role, **kwargs)

    def by_text(self, text: str, exact: bool = False) -> Locator:
        """通过可见文本定位。"""
        return self.page.get_by_text(text, exact=exact)

    def by_label(self, label: str, exact: bool | None = None) -> Locator:
        """通过表单 label 定位。"""
        kwargs: dict = {}
        if exact is not None:
            kwargs["exact"] = exact
        return self.page.get_by_label(label, **kwargs)

    def by_placeholder(self, placeholder: str, exact: bool | None = None) -> Locator:
        """通过 placeholder 定位。"""
        kwargs: dict = {}
        if exact is not None:
            kwargs["exact"] = exact
        return self.page.get_by_placeholder(placeholder, **kwargs)

    def by_test_id(self, test_id: str) -> Locator:
        """通过 data-testid 定位。"""
        return self.page.get_by_test_id(test_id)

    def by_alt_text(self, alt: str, exact: bool | None = None) -> Locator:
        """通过图片 alt 文本定位。"""
        kwargs: dict = {}
        if exact is not None:
            kwargs["exact"] = exact
        return self.page.get_by_alt_text(alt, **kwargs)

    def by_title(self, title: str, exact: bool | None = None) -> Locator:
        """通过 title 属性定位。"""
        kwargs: dict = {}
        if exact is not None:
            kwargs["exact"] = exact
        return self.page.get_by_title(title, **kwargs)

    # ── 交互动作 ───────────────────────────────────────────────────

    def click(
        self,
        locator: Locator,
        timeout: int | None = None,
        force: bool = False,
    ) -> None:
        """点击元素。"""
        locator.click(timeout=timeout, force=force)

    def double_click(self, locator: Locator, timeout: int | None = None) -> None:
        """双击元素。"""
        locator.dblclick(timeout=timeout)

    def fill(
        self,
        locator: Locator,
        value: str,
        timeout: int | None = None,
    ) -> None:
        """填写输入框（会先清空）。"""
        locator.fill(value, timeout=timeout)

    def type_text(
        self,
        locator: Locator,
        value: str,
        delay: int = 0,
        timeout: int | None = None,
    ) -> None:
        """逐字输入（不清空）。"""
        locator.type(value, delay=delay, timeout=timeout)

    def clear(self, locator: Locator, timeout: int | None = None) -> None:
        """清空输入框。"""
        locator.fill("", timeout=timeout)

    def select_option(
        self,
        locator: Locator,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
        timeout: int | None = None,
    ) -> None:
        """选择下拉框选项（value/label/index 三选一）。"""
        if value is not None:
            locator.select_option(value=value, timeout=timeout)
        elif label is not None:
            locator.select_option(label=label, timeout=timeout)
        elif index is not None:
            locator.select_option(index=index, timeout=timeout)
        else:
            raise ValueError("select_option 需要 value、label 或 index 之一")

    def check(self, locator: Locator, timeout: int | None = None) -> None:
        """勾选复选框。"""
        locator.check(timeout=timeout)

    def uncheck(self, locator: Locator, timeout: int | None = None) -> None:
        """取消勾选。"""
        locator.uncheck(timeout=timeout)

    def press_key(self, locator: Locator, key: str) -> None:
        """在元素上按键。"""
        locator.press(key)

    def hover(self, locator: Locator) -> None:
        """鼠标悬停。"""
        locator.hover()

    def focus(self, locator: Locator) -> None:
        """聚焦元素。"""
        locator.focus()

    def scroll_into_view(self, locator: Locator) -> None:
        """滚动元素到可见区域。"""
        locator.scroll_into_view_if_needed()

    # ── 读取 ───────────────────────────────────────────────────────

    def get_text(self, locator: Locator, timeout: int | None = None) -> str:
        """返回元素 textContent。"""
        return locator.text_content(timeout=timeout) or ""

    def get_inner_text(self, locator: Locator, timeout: int | None = None) -> str:
        """返回元素 innerText。"""
        return locator.inner_text(timeout=timeout)

    def get_input_value(self, locator: Locator, timeout: int | None = None) -> str:
        """返回输入框的值。"""
        return locator.input_value(timeout=timeout)

    def get_attribute(
        self,
        locator: Locator,
        name: str,
        timeout: int | None = None,
    ) -> str | None:
        """返回元素属性。"""
        return locator.get_attribute(name, timeout=timeout)

    def is_visible(self, locator: Locator) -> bool:
        """元素是否可见。"""
        return locator.is_visible()

    def is_enabled(self, locator: Locator) -> bool:
        """元素是否启用。"""
        return locator.is_enabled()

    def is_checked(self, locator: Locator) -> bool:
        """复选框是否勾选。"""
        return locator.is_checked()

    def is_disabled(self, locator: Locator) -> bool:
        """元素是否禁用。"""
        return locator.is_disabled()

    # ── 等待 ───────────────────────────────────────────────────────

    def wait_for_url(self, url: str | None = None, timeout: int | None = None) -> None:
        """等待 URL 变化。"""
        if url:
            self.page.wait_for_url(url, timeout=timeout)

    def wait_for_selector(
        self,
        locator: Locator,
        state: str = "visible",
        timeout: int | None = None,
    ) -> None:
        """等待元素状态。"""
        locator.wait_for(state=state, timeout=timeout)

    def wait_for_visible(self, locator: Locator, timeout: int | None = None) -> None:
        """等待元素可见。"""
        locator.wait_for(state="visible", timeout=timeout)

    def wait_for_hidden(self, locator: Locator, timeout: int | None = None) -> None:
        """等待元素隐藏。"""
        locator.wait_for(state="hidden", timeout=timeout)

    # ── 断言 ───────────────────────────────────────────────────────

    def assert_visible(self, locator: Locator, timeout: int = 5_000) -> None:
        """断言元素可见。"""
        expect(locator).to_be_visible(timeout=timeout)

    def assert_hidden(self, locator: Locator, timeout: int = 5_000) -> None:
        """断言元素隐藏。"""
        expect(locator).to_be_hidden(timeout=timeout)

    def assert_enabled(self, locator: Locator, timeout: int = 5_000) -> None:
        """断言元素启用。"""
        expect(locator).to_be_enabled(timeout=timeout)

    def assert_disabled(self, locator: Locator, timeout: int = 5_000) -> None:
        """断言元素禁用。"""
        expect(locator).to_be_disabled(timeout=timeout)

    def assert_text(
        self,
        locator: Locator,
        expected: str,
        timeout: int = 5_000,
    ) -> None:
        """断言元素包含文本。"""
        expect(locator).to_contain_text(expected, timeout=timeout)

    def assert_value(
        self,
        locator: Locator,
        expected: str,
        timeout: int = 5_000,
    ) -> None:
        """断言输入框包含值。"""
        expect(locator).to_have_value(expected, timeout=timeout)

    def assert_url_contains(self, fragment: str) -> None:
        """断言当前 URL 包含片段。"""
        current = self.get_url()
        assert fragment in current, f"URL {current!r} 不包含 {fragment!r}"

    def assert_text_visible(self, text: str, timeout: int = 5_000) -> None:
        """断言页面存在可见文本。"""
        expect(self.page.get_by_text(text, exact=False).first).to_be_visible(
            timeout=timeout
        )

    # ── Toast ──────────────────────────────────────────────────────

    @property
    def toast(self) -> Locator:
        """第一个可见 Toast/消息容器。"""
        return self.page.locator(self.TOAST_SELECTOR).first

    def wait_for_toast(
        self,
        text: str | None = None,
        timeout: int = 8_000,
    ) -> str:
        """等待 Toast 出现并返回文本。"""
        locator = self.page.locator(self.TOAST_SELECTOR)
        if text:
            locator = locator.filter(has_text=text)
        locator.first.wait_for(state="visible", timeout=timeout)
        return locator.first.inner_text()

    def assert_toast(self, text: str, timeout: int = 8_000) -> None:
        """断言包含指定文本的 Toast 可见。"""
        locator = self.page.locator(self.TOAST_SELECTOR).filter(has_text=text)
        expect(locator.first).to_be_visible(timeout=timeout)

    # ── 弹窗 ───────────────────────────────────────────────────────

    @property
    def dialog(self) -> Locator:
        """第一个可见弹窗/抽屉容器。"""
        return self.page.locator(self.DIALOG_SELECTOR).first

    def wait_for_dialog(
        self,
        text: str | None = None,
        timeout: int = 8_000,
    ) -> Locator:
        """等待弹窗出现（可按文本过滤）。"""
        locator = self.page.locator(self.DIALOG_SELECTOR)
        if text:
            locator = locator.filter(has_text=text)
        locator.first.wait_for(state="visible", timeout=timeout)
        return locator.first

    def assert_dialog_visible(
        self,
        text: str | None = None,
        timeout: int = 8_000,
    ) -> None:
        """断言弹窗可见。"""
        locator = self.page.locator(self.DIALOG_SELECTOR)
        if text:
            locator = locator.filter(has_text=text)
        expect(locator.first).to_be_visible(timeout=timeout)

    def close_dialog(self, timeout: int = 5_000) -> None:
        """点击弹窗内的取消/关闭按钮。"""
        dialog = self.dialog
        button = dialog.get_by_role("button", name=self.CANCEL_BUTTON_PATTERN)
        if button.count() == 0:
            button = dialog.locator("button").filter(has_text=self.CANCEL_BUTTON_PATTERN)
        button.last.click(timeout=timeout)

    def accept_next_dialog(self) -> None:
        """接受下一个浏览器原生弹窗。"""
        self.page.once("dialog", lambda dialog: dialog.accept())

    def dismiss_next_dialog(self) -> None:
        """关闭下一个浏览器原生弹窗。"""
        self.page.once("dialog", lambda dialog: dialog.dismiss())

    def handle_unsaved_changes(
        self,
        accept: bool = True,
        timeout: int = 8_000,
    ) -> None:
        """处理“未保存离开”确认框。"""
        dialog = self.wait_for_dialog(
            text="未保存|离开|放弃|确认离开|确定离开",
            timeout=timeout,
        )
        pattern = self.CONFIRM_BUTTON_PATTERN if accept else self.CANCEL_BUTTON_PATTERN
        button = dialog.get_by_role("button", name=pattern)
        if button.count() == 0:
            button = dialog.locator("button").filter(has_text=pattern)
        button.last.click(timeout=timeout)

    # ── 表格 / 页面文本 ────────────────────────────────────────────

    def get_table(self, scope: str | Locator | None = None) -> Locator:
        """返回 scope 内（或整页）第一个表格。"""
        root = self.page if scope is None else (
            scope if isinstance(scope, Locator) else self.page.locator(scope)
        )
        return root.locator(self.TABLE_SELECTOR).first

    def get_table_rows(self, scope: str | Locator | None = None) -> Locator:
        """返回表格 tbody 行。"""
        return self.get_table(scope).locator("tbody tr")

    def get_page_text(self) -> str:
        """返回页面可见文本。"""
        return self.page.locator("body").inner_text()

    # ── 截图 ───────────────────────────────────────────────────────

    def take_screenshot(self, name: str, full_page: bool = False) -> str:
        """截图到 ``artifacts/screenshots``。"""
        screenshot_dir = Path(self.config.screenshot_dir)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = screenshot_dir / f"{name}.png"
        self.page.screenshot(path=str(path), full_page=full_page)
        return str(path.absolute())

    def screenshot_on_failure(self, test_name: str | None = None) -> str | None:
        """失败截图；异常不会覆盖原始失败。"""
        safe_name = re.sub(r"[^0-9A-Za-z_.-]+", "_", test_name or "failure")[:120]
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            return self.take_screenshot(f"{safe_name}-{timestamp}", full_page=True)
        except Exception:
            return None

    def screenshot_path(self, name: str) -> Path:
        """返回预期截图路径。"""
        return Path(self.config.screenshot_dir) / f"{name}.png"
