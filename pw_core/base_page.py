import re
from urllib.parse import urlparse

import allure
from playwright.sync_api import Page, Locator, expect
from pw_core.driver.driver_manager import DriverManager
from utils.logger import get_logger

log = get_logger(__name__)


class BasePage:
    """
    所有页面对象的父类。
    封装 Playwright 交互，提供日志记录、Allure 步骤和智能等待。
    """

    def __init__(self):
        self.driver_manager = DriverManager()
        self.page: Page = self.driver_manager.get_driver().page

    # ------------------------------------------------------------------
    # 导航相关
    # ------------------------------------------------------------------

    def get_current_url(self) -> str:
        return self.page.url

    def get_title(self) -> str:
        return self.page.title()

    def go_back(self):
        log.debug("浏览器后退")
        with allure.step("后退"):
            self.page.go_back()

    def refresh(self):
        log.debug("刷新页面")
        with allure.step("刷新页面"):
            self.page.reload(wait_until="domcontentloaded")

    # ------------------------------------------------------------------
    # 首页初始化 / 用例后置
    # ------------------------------------------------------------------

    def close_intro_guide(self) -> None:
        """关闭 intro.js 新手引导遮罩；没有引导时立即返回。"""
        guide = self.page.locator(".introjs-overlay, .introjs-tooltip")
        if guide.count() == 0:
            return

        for selector in (
            ".introjs-skipbutton",
            ".introjs-donebutton",
            ".introjs-tooltip [class*='close']",
        ):
            buttons = self.page.locator(selector)
            for index in range(buttons.count()):
                item = buttons.nth(index)
                try:
                    if item.is_visible():
                        item.click(force=True)
                        self.page.wait_for_timeout(200)
                        break
                except Exception:
                    continue
            if guide.count() == 0:
                return

        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(200)
        if guide.count() == 0:
            return

        self.page.evaluate(
            """() => {
                try { if (window.introJs) window.introJs().exit(true); } catch (e) {}
                document.querySelectorAll(
                    '.introjs-overlay, .introjs-tooltipReferenceLayer, .introjs-helperLayer, .introjs-tooltip'
                ).forEach(el => el.remove());
                document.querySelectorAll('[aria-hidden="true"]').forEach(
                    el => el.removeAttribute('aria-hidden')
                );
            }"""
        )
        self.page.wait_for_timeout(100)

    def close_visible_modals(self) -> None:
        """关闭当前可见的 Ant Design 弹窗，为回首页清场。"""
        for _ in range(3):
            wraps = self.page.locator(".ant-modal-wrap:visible")
            if wraps.count() == 0:
                return

            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)
            if self.page.locator(".ant-modal-wrap:visible").count() == 0:
                return

            close_buttons = self.page.locator(".ant-modal-close:visible")
            if close_buttons.count():
                try:
                    close_buttons.first.click(force=True)
                except Exception:
                    pass
                self.page.wait_for_timeout(200)
                continue

            for label in (r"取\s*消", r"关\s*闭"):
                buttons = self.page.get_by_role("button", name=re.compile(label))
                clicked = False
                for index in range(buttons.count()):
                    item = buttons.nth(index)
                    try:
                        if item.is_visible():
                            item.click(force=True)
                            clicked = True
                            break
                    except Exception:
                        continue
                if clicked:
                    break
            self.page.wait_for_timeout(200)

    @allure.step("回到 ERP 首页")
    def back_to_home(self, home_url: str | None = None) -> None:
        """关闭引导/弹窗并回到 ERP 首页，供用例后置和测试类初始化使用。"""
        self.close_intro_guide()
        self.close_visible_modals()

        if home_url is None:
            parsed = urlparse(self.page.url)
            home_url = f"{parsed.scheme}://{parsed.netloc}/dashboard/analysis"

        clicked = False
        home_links = self.page.locator('a[href="/dashboard/analysis"]:visible')
        if home_links.count():
            try:
                home_links.first.click()
                clicked = True
            except Exception:
                clicked = False

        if not clicked:
            self.page.goto(home_url, wait_until="domcontentloaded")

        try:
            self.page.wait_for_url(
                re.compile(r".*/dashboard/analysis.*"),
                timeout=60000,
            )
        except Exception:
            self.page.goto(home_url, wait_until="domcontentloaded")
            self.page.wait_for_url(
                re.compile(r".*/dashboard/analysis.*"),
                timeout=60000,
            )

        self.page.locator(".ant-menu:visible").first.wait_for(
            state="visible",
            timeout=60000,
        )
        self.page.evaluate("window.scrollTo(0, 0)")
        log.info("已回到 ERP 首页")

    # ------------------------------------------------------------------
    # 元素获取
    # ------------------------------------------------------------------

    def get_element(self, locator: str) -> Locator:
        return self.page.locator(locator)

    def get_elements(self, locator: str) -> Locator:
        return self.page.locator(locator)

    # ------------------------------------------------------------------
    # 交互操作
    # ------------------------------------------------------------------

    def click(self, locator: str, description: str = ""):
        label = description or locator
        log.info(f"点击: {label}")
        with allure.step(f"点击 '{label}'"):
            self.page.locator(locator).click()

    def click_with_js(self, locator: str, description: str = ""):
        label = description or locator
        log.info(f"JS 点击: {label}")
        with allure.step(f"JS 点击 '{label}'"):
            self.page.locator(locator).evaluate("el => el.click()")

    def fill(self, locator: str, value: str, description: str = "", sensitive: bool = False):
        """
        填写输入框。

        参数:
            locator: 元素定位器
            value: 要填写的值
            description: 操作描述（可选）
            sensitive: 是否为敏感信息（如密码），True 时日志中会脱敏
        """
        label = description or locator
        display_value = "***" if sensitive else value
        log.info(f"填写 '{label}' 内容: {display_value}")
        with allure.step(f"填写 '{label}' → '{display_value}'"):
            self.page.locator(locator).fill(value)

    def clear_and_fill(self, locator: str, value: str, description: str = "", sensitive: bool = False):
        label = description or locator
        display_value = "***" if sensitive else value
        log.info(f"清空并填写 '{label}' 内容: {display_value}")
        with allure.step(f"清空并填写 '{label}' → '{display_value}'"):
            self.page.locator(locator).clear()
            self.page.locator(locator).fill(value)

    def press_key(self, locator: str, key: str):
        log.debug(f"在 {locator} 上按下按键 '{key}'")
        with allure.step(f"按下 '{key}'"):
            self.page.locator(locator).press(key)

    def select_option(self, locator: str, value: str, description: str = ""):
        label = description or locator
        log.info(f"在 '{label}' 中选择 '{value}'")
        with allure.step(f"在 '{label}' 中选择 '{value}'"):
            self.page.locator(locator).select_option(value)

    def hover(self, locator: str, description: str = ""):
        label = description or locator
        log.debug(f"悬停在: {label}")
        with allure.step(f"悬停 '{label}'"):
            self.page.locator(locator).hover()

    def scroll_into_view(self, locator: str):
        self.page.locator(locator).scroll_into_view_if_needed()

    def scroll_to_bottom(self):
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    # ------------------------------------------------------------------
    # 等待
    # ------------------------------------------------------------------

    def wait_for_visible(self, locator: str, timeout: int = None):
        kwargs = {"state": "visible"}
        if timeout is not None:
            kwargs["timeout"] = timeout
        self.page.locator(locator).wait_for(**kwargs)

    def wait_for_hidden(self, locator: str, timeout: int = None):
        kwargs = {"state": "hidden"}
        if timeout is not None:
            kwargs["timeout"] = timeout
        self.page.locator(locator).wait_for(**kwargs)

    def wait_for_url(self, url_pattern: str):
        self.page.wait_for_url(url_pattern)

    def wait_for_load(self):
        self.page.wait_for_load_state("domcontentloaded")

    # ------------------------------------------------------------------
    # 读取值
    # ------------------------------------------------------------------

    def get_text(self, locator: str) -> str:
        text = self.page.locator(locator).inner_text()
        log.debug(f"'{locator}' 的文本: {text}")
        return text

    def get_attribute(self, locator: str, attribute: str) -> str:
        return self.page.locator(locator).get_attribute(attribute)

    def get_value(self, locator: str) -> str:
        return self.page.locator(locator).input_value()

    def get_all_texts(self, locator: str) -> list[str]:
        return self.page.locator(locator).all_inner_texts()

    def get_count(self, locator: str) -> int:
        return self.page.locator(locator).count()

    # ------------------------------------------------------------------
    # 状态检查
    # ------------------------------------------------------------------

    def is_visible(self, locator: str) -> bool:
        return self.page.locator(locator).is_visible()

    def is_enabled(self, locator: str) -> bool:
        return self.page.locator(locator).is_enabled()

    def is_checked(self, locator: str) -> bool:
        return self.page.locator(locator).is_checked()

    # ------------------------------------------------------------------
    # 断言
    # ------------------------------------------------------------------

    def assert_visible(self, locator: str, message: str = ""):
        log.debug(f"断言可见: {locator}")
        with allure.step(f"断言可见: {message or locator}"):
            expect(self.page.locator(locator)).to_be_visible()

    def assert_hidden(self, locator: str, message: str = ""):
        log.debug(f"断言隐藏: {locator}")
        with allure.step(f"断言隐藏: {message or locator}"):
            expect(self.page.locator(locator)).to_be_hidden()

    def assert_text(self, locator: str, expected: str):
        log.debug(f"断言文本 '{expected}' 在: {locator}")
        with allure.step(f"断言文本 = '{expected}'"):
            expect(self.page.locator(locator)).to_have_text(expected)

    def assert_text_contains(self, locator: str, expected: str):
        log.debug(f"断言文本包含 '{expected}' 在: {locator}")
        with allure.step(f"断言文本包含 '{expected}'"):
            expect(self.page.locator(locator)).to_contain_text(expected)

    def assert_url_contains(self, partial_url: str):
        import re
        log.debug(f"断言 URL 包含: {partial_url}")
        with allure.step(f"断言 URL 包含 '{partial_url}'"):
            expect(self.page).to_have_url(re.compile(re.escape(partial_url)))

    def assert_title_contains(self, partial_title: str):
        log.debug(f"断言标题包含: {partial_title}")
        with allure.step(f"断言标题包含 '{partial_title}'"):
            expect(self.page).to_have_title(f"*{partial_title}*")

    def assert_count(self, locator: str, expected_count: int):
        log.debug(f"断言数量 {expected_count} 对于: {locator}")
        with allure.step(f"断言数量 = {expected_count}"):
            expect(self.page.locator(locator)).to_have_count(expected_count)

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------

    def take_screenshot(self, name: str = "screenshot") -> bytes:
        return self.driver_manager.get_driver().take_screenshot(name)
