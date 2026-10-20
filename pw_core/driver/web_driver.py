from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from utils.config import Config
from utils.logger import get_logger

log = get_logger(__name__)


class WebDriver:

    def __init__(self):
        self._playwright = None
        self._browser: Browser = None
        self._context: BrowserContext = None
        self._page: Page = None

    def start(self, base_url: str = "", storage_state=None) -> "WebDriver":
        url = base_url or Config.BASE_URL
        log.info(f"启动浏览器：{Config.BROWSER} | headless = {Config.HEADLESS} | base_url = {url}")
        self._playwright = sync_playwright().start()

        browser_launcher = getattr(self._playwright, Config.BROWSER)
        self._browser = browser_launcher.launch(
            headless=Config.HEADLESS,
            slow_mo=Config.SLOW_MO,
        )

        context_kwargs = {
            "base_url": url,
            "viewport": {"width": 1440, "height": 900},
            "record_video_dir": "reports/videos" if Config.VIDEO_ON_FAILURE else None,
        }
        if storage_state:
            context_kwargs["storage_state"] = storage_state

        self._context = self._browser.new_context(**context_kwargs)

        self._context.set_default_timeout(Config.TIMEOUT)
        self._page = self._context.new_page()
        log.info("浏览器启动成功")
        return self

    def save_storage_state(self, path: str | Path) -> dict:
        """保存当前 context 的 cookies + localStorage 快照。"""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        state = self._context.storage_state(path=str(target))
        log.info(f"登录快照已保存：{target}")
        return state

    def quit(self):
        log.info("关闭浏览器")
        if self._page:
            self._page.close()
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def take_screenshot(self, name: str = "screenshot") -> bytes:
        path = f"reports/screenshots/{name}.png"
        self._page.screenshot(path=path, full_page=True)
        log.info(f"Screenshot saved: {path}")
        return self._page.screenshot(full_page=True)

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("浏览器未启动")
        return self._page
