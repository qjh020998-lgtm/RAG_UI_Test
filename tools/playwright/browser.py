"""Browser lifecycle management for Playwright."""

import os
from pathlib import Path
from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)
from tools.playwright.conf import PlaywrightConfig

class BrowserManager:
    """浏览器生命周期管理器。
    
    负责启动/关闭浏览器，创建浏览器上下文和页面。
    支持配置持久化和 trace 录制。
    """
    
    def __init__(self, config: PlaywrightConfig | None = None):
        self.config = config or PlaywrightConfig()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def start_browser(self) -> Browser:
        """启动浏览器实例。

        Returns:
            Browser 对象
        """
        if self._browser is not None:
            return self._browser

        # 设置自定义浏览器路径（如果存在）
        if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            custom_browser_path = r"D:\PlaywrightBrowsers"
            if Path(custom_browser_path).exists():
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = custom_browser_path

        # 避免重复初始化 playwright（防止 asyncio 冲突）
        if self._playwright is None:
            self._playwright = sync_playwright().start()

        browser_type = self.config.browser_type.value
        browser_launcher = getattr(self._playwright, browser_type)

        self._browser = browser_launcher.launch(
            headless=self.config.headless,
            slow_mo=self.config.slow_mo,
        )

        return self._browser
    
    def new_context(
        self,
        viewport_width: int | None = None,
        viewport_height: int | None = None,
        locale: str | None = None,
        timezone_id: str | None = None,
        ignore_https_errors: bool | None = None,
        record_trace: bool = False,
    ) -> BrowserContext:
        """创建新的浏览器上下文。
        
        Args:
            viewport_width: 视口宽度
            viewport_height: 视口高度
            locale: 语言设置
            timezone_id: 时区设置
            ignore_https_errors: 是否忽略 HTTPS 错误
            record_trace: 是否录制 trace
            
        Returns:
            BrowserContext 对象
        """
        if self._browser is None:
            self.start_browser()
        
        kwargs = {
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
        }
        
        self._context = self._browser.new_context(**kwargs)
        
        if record_trace or self.config.trace_enabled:
            trace_dir = Path(self.config.trace_dir)
            trace_dir.mkdir(parents=True, exist_ok=True)
            
            self._context.tracing.start(
                screenshots=True,
                snapshots=True,
                sources=True,
            )
        
        self._context.set_default_timeout(self.config.default_timeout)
        self._context.set_default_navigation_timeout(
            self.config.navigation_timeout
        )
        
        return self._context
    
    def new_page(self) -> Page:
        """创建新页面。
        
        Returns:
            Page 对象
        """
        if self._context is None:
            self.new_context()
        
        self._page = self._context.new_page()
        return self._page
    
    def get_page(self) -> Page:
        """获取当前页面（如果没有则创建）。
        
        Returns:
            Page 对象
        """
        if self._page is None:
            return self.new_page()
        return self._page
    
    def close_page(self) -> None:
        """关闭当前页面。"""
        if self._page is not None:
            self._page.close()
            self._page = None
    
    def close_context(self, trace_name: str | None = None) -> None:
        """关闭浏览器上下文。
        
        Args:
            trace_name: trace 文件名（如果提供了此参数且开启了 trace）
        """
        if self._context is not None:
            if trace_name and self.config.trace_enabled:
                trace_dir = Path(self.config.trace_dir)
                trace_dir.mkdir(parents=True, exist_ok=True)
                trace_path = trace_dir / f"{trace_name}.zip"
                
                self._context.tracing.stop(path=str(trace_path))
            
            self._context.close()
            self._context = None
    
    def close_browser(self) -> None:
        """关闭浏览器。"""
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
    
    def close_all(self, trace_name: str | None = None) -> None:
        """关闭所有资源。
        
        Args:
            trace_name: trace 文件名
        """
        self.close_page()
        self.close_context(trace_name=trace_name)
        self.close_browser()
    
    def __enter__(self) -> "BrowserManager":
        """支持 with 语句。"""
        self.start_browser()
        self.new_context()
        self.new_page()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """退出 with 语句时清理资源。"""
        self.close_all()
