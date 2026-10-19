"""Playwright WebDriver：浏览器、context、page 的生命周期。

合并了原 ``BrowserManager`` 与 ``WebDriver`` 两层，避免重复封装：

- 浏览器是会话级，只启动一次；
- context/page 是测试级，保证用例隔离；
- 支持 trace、storage_state、失败截图；
- 对上层只暴露 ``page`` / ``new_context`` / ``new_page`` 等必要接口。
"""

from __future__ import annotations

import os
from pathlib import Path
from utils.config import Config
from utils.logger import get_logger
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

from pw_core.conf import PlaywrightConfig
log = get_logger(__name__)

class WebDriver:
    """Playwright 浏览器生命周期管理器。"""

    def __init__(self, config: PlaywrightConfig | None = None):
        self.config = config or PlaywrightConfig()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: list[BrowserContext] = []
        self._trace_contexts: set[int] = set()
        self._last_page: Page | None = None

    # ── 浏览器 ────────────────────────────────────────────────────

    def start(self) -> "WebDriver":
        """启动 Playwright 与浏览器（幂等）。"""
        if self._browser is not None:
            return self

        if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            custom_browser_path = r"D:\PlaywrightBrowsers"
            if Path(custom_browser_path).exists():
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = custom_browser_path

        if self._playwright is None:
            self._playwright = sync_playwright().start()

        browser_type = self.config.browser_type.value
        browser_launcher = getattr(self._playwright, browser_type)
        self._browser = browser_launcher.launch(
            headless=self.config.headless,
            slow_mo=self.config.slow_mo,
        )
        return self

    # 兼容旧调用
    start_browser = start

    @property
    def browser(self) -> Browser:
        """当前浏览器进程。"""
        if self._browser is None:
            self.start()
        assert self._browser is not None
        return self._browser

    # ── context / page ─────────────────────────────────────────────

    def new_context(
        self,
        *,
        viewport_width: int | None = None,
        viewport_height: int | None = None,
        locale: str | None = None,
        timezone_id: str | None = None,
        ignore_https_errors: bool | None = None,
        record_trace: bool | None = None,
        storage_state: str | Path | None = None,
        accept_downloads: bool = True,
    ) -> BrowserContext:
        """创建隔离的浏览器上下文。"""
        if self._browser is None:
            self.start()

        kwargs: dict = {
            "viewport": {
                "width": viewport_width or self.config.viewport_width,
                "height": viewport_height or self.config.viewport_height,
            },
            "locale": locale or self.config.locale,
            "timezone_id": timezone_id or self.config.timezone_id,
            "ignore_https_errors": (
                ignore_https_errors
                if ignore_https_errors is not None
                else self.config.ignore_https_errors
            ),
            "accept_downloads": accept_downloads,
        }

        if storage_state:
            state_path = Path(storage_state)
            if state_path.exists():
                kwargs["storage_state"] = str(state_path)

        assert self._browser is not None
        context = self._browser.new_context(**kwargs)

        should_trace = (
            self.config.trace_enabled if record_trace is None else record_trace
        )
        if should_trace:
            context.tracing.start(
                screenshots=self.config.trace_screenshots,
                snapshots=self.config.trace_snapshots,
                sources=self.config.trace_sources,
            )
            self._trace_contexts.add(id(context))

        context.set_default_timeout(self.config.default_timeout)
        context.set_default_navigation_timeout(self.config.navigation_timeout)
        self._contexts.append(context)
        return context

    def new_page(self, context: BrowserContext | None = None) -> Page:
        """在指定 context（或最近 context）中创建页面。"""
        if context is None:
            if self._contexts:
                context = self._contexts[-1]
            else:
                context = self.new_context()

        page = context.new_page()
        self._last_page = page
        return page

    def get_page(self) -> Page:
        """返回最近创建的页面，没有则创建。"""
        if self._last_page is None or self._last_page.is_closed():
            return self.new_page()
        return self._last_page

    @property
    def current_context(self) -> BrowserContext | None:
        """最近创建的 context。"""
        return self._contexts[-1] if self._contexts else None

    @property
    def page(self) -> Page:
        """当前页面。"""
        return self.get_page()

    def goto(self, url: str, wait_until: str = "load") -> None:
        """当前页面导航。"""
        self.page.goto(url, wait_until=wait_until)

    def save_storage_state(
        self,
        context: BrowserContext,
        path: str | Path | None = None,
    ) -> str:
        """保存当前 context 的 cookies/localStorage。"""
        state_path = Path(path or self.config.auth_state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_path))
        return str(state_path)

    def screenshot(self, name: str, full_page: bool = False) -> str:
        """截图到 ``artifacts/screenshots``。"""
        screenshot_dir = Path(self.config.screenshot_dir)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = screenshot_dir / f"{name}.png"
        self.page.screenshot(path=str(path), full_page=full_page)
        return str(path.absolute())

    def close_page(self, page: Page | None = None) -> None:
        """关闭指定页面或最近页面。"""
        target = page or self._last_page
        if target is not None and not target.is_closed():
            target.close()
        if target is self._last_page:
            self._last_page = None

    def close_context(
        self,
        context: BrowserContext | None = None,
        trace_name: str | None = None,
    ) -> None:
        """停止 trace 并关闭指定 context。"""
        if context is None:
            if not self._contexts:
                return
            context = self._contexts[-1]

        context_key = id(context)
        if context_key in self._trace_contexts:
            try:
                if trace_name:
                    trace_dir = Path(self.config.trace_dir)
                    trace_dir.mkdir(parents=True, exist_ok=True)
                    safe_name = "".join(
                        char if char.isalnum() or char in "-_." else "_"
                        for char in trace_name
                    )
                    context.tracing.stop(path=str(trace_dir / f"{safe_name}.zip"))
                else:
                    context.tracing.stop()
            finally:
                self._trace_contexts.discard(context_key)

        try:
            context.close()
        finally:
            if context in self._contexts:
                self._contexts.remove(context)

    def close_browser(self) -> None:
        """关闭浏览器并停止 Playwright。"""
        if self._browser is not None:
            self._browser.close()
            self._browser = None

        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def close(self, trace_name: str | None = None) -> None:
        """关闭全部 context、页面和浏览器。"""
        for context in list(self._contexts):
            self.close_context(context, trace_name=trace_name)
        self.close_page()
        self.close_browser()

    # ── 上下文管理器 ───────────────────────────────────────────────

    def __enter__(self) -> "WebDriver":
        self.start()
        self.new_context()
        self.new_page()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
