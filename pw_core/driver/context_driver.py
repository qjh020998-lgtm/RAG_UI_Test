"""用例级驱动包装：只持有 context/page，不负责浏览器生命周期。"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import BrowserContext, Page

from utils.logger import get_logger

log = get_logger(__name__)


class ContextDriver:
    """把 BrowserContext + Page 包装成 Page Object 需要的 driver。"""

    def __init__(self, context: BrowserContext, page: Page) -> None:
        self._context = context
        self._page = page

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("页面已关闭")
        return self._page

    def take_screenshot(self, name: str = "screenshot") -> bytes:
        path = Path("reports/screenshots") / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)
        log.info(f"Screenshot saved: {path}")
        return self.page.screenshot(full_page=True)

    def quit(self) -> None:
        """关闭当前 context/page。"""
        log.info("关闭 context/page")
        if self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
