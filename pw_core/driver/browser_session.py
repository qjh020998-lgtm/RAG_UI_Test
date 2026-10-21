"""测试类级浏览器会话：一个测试类只启动一次浏览器。"""

from __future__ import annotations

from playwright.sync_api import Browser, sync_playwright

from utils.config import Config
from utils.logger import get_logger

log = get_logger(__name__)


class BrowserSession:
    """管理 Playwright + Browser 的类级生命周期。"""

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None

    def start(self) -> "BrowserSession":
        log.info(
            f"启动测试类浏览器：{Config.BROWSER} | "
            f"headless = {Config.HEADLESS} | fullscreen = {Config.FULLSCREEN}"
        )
        self._playwright = sync_playwright().start()
        launcher = getattr(self._playwright, Config.BROWSER)

        launch_kwargs = {
            "headless": Config.HEADLESS,
            "slow_mo": Config.SLOW_MO,
        }
        # Chromium 系浏览器在 headed 模式下用 --start-fullscreen 进入全屏；
        # 无头模式没有真实窗口，用大视口模拟全屏。
        if (
            Config.FULLSCREEN
            and not Config.HEADLESS
            and Config.BROWSER in {"chromium", "chrome", "msedge"}
        ):
            # 固定 1920x1080 页面；同时给浏览器窗口一个匹配的初始尺寸
            launch_kwargs["args"] = [
                f"--window-size={Config.VIEWPORT_WIDTH},{Config.VIEWPORT_HEIGHT}"
            ]

        self._browser = launcher.launch(**launch_kwargs)
        log.info("测试类浏览器启动成功")
        return self

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            raise RuntimeError("浏览器尚未启动")
        return self._browser

    def is_connected(self) -> bool:
        return bool(self._browser and self._browser.is_connected())

    def new_context(self, base_url: str, storage_state=None):
        if not self.is_connected():
            log.warning("测试类浏览器已断开，重新启动")
            self.close()
            self.start()

        context_kwargs = {
            "base_url": base_url,
            "record_video_dir": "reports/videos" if Config.VIDEO_ON_FAILURE else None,
        }
        if Config.FULLSCREEN:
            # 页面统一使用 1920x1080 全屏视口
            context_kwargs["viewport"] = {
                "width": Config.VIEWPORT_WIDTH,
                "height": Config.VIEWPORT_HEIGHT,
            }
        else:
            context_kwargs["viewport"] = {"width": 1440, "height": 900}
        if storage_state:
            context_kwargs["storage_state"] = storage_state

        context = self.browser.new_context(**context_kwargs)
        context.set_default_timeout(Config.TIMEOUT)
        return context

    def close(self) -> None:
        log.info("关闭测试类浏览器")
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
