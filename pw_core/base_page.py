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
