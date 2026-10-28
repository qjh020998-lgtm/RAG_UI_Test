"""登录页 Page Object。"""

import re

from playwright.sync_api import expect

from data_loader import load_config
from infrastructure.pw_core.base_page import BasePage


class LoginPage(BasePage):
    """ERP 登录页面 (http://<host>/user/login)。"""

    # --- Locators ---
    USERNAME_INPUT = "#loginName"
    USERNAME_PLACEHOLDER = "input[placeholder='请输入用户名']"
    PASSWORD_INPUT = "#password"
    PASSWORD_PLACEHOLDER = "input[placeholder='请输入密码']"
    LOGIN_BUTTON = ".login-button"

    def __init__(self) -> None:
        super().__init__()
        self.base_url: str = load_config().base_url

    # --- Actions ---

    def goto_login(self) -> None:
        """导航到登录页并等待页面加载完成。"""
        # 不等待 load：登录页引入了外部统计脚本，可能长期不触发 load
        self.page.goto(f"{self.base_url}/user/login", wait_until="domcontentloaded")
        self.wait_for_load()
        # 兼容旧版 id 选择器与 Ant Design 仅 placeholder 的输入框
        self.page.locator(
            f"{self.USERNAME_INPUT}, {self.USERNAME_PLACEHOLDER}"
        ).first.wait_for(state="visible", timeout=30000)

    def login(self, username: str, password: str) -> None:
        """填写账号密码并点击登录。"""
        username_locator = (
            self.USERNAME_INPUT
            if self.is_visible(self.USERNAME_INPUT)
            else self.USERNAME_PLACEHOLDER
        )
        password_locator = (
            self.PASSWORD_INPUT
            if self.is_visible(self.PASSWORD_INPUT)
            else self.PASSWORD_PLACEHOLDER
        )
        self.fill(username_locator, username, "用户名")
        self.fill(password_locator, password, "密码", sensitive=True)
        self.click(self.LOGIN_BUTTON, "登录按钮")

    def login_and_wait(
        self,
        username: str,
        password: str,
        expect_success: bool = True,
        timeout: int = 15000,
    ) -> None:
        """登录并等待结果。"""
        self.login(username, password)
        if expect_success:
            expect(self.page).not_to_have_url(
                re.compile(r".*/user/login/?(?:\?.*)?$"),
                timeout=timeout,
            )
        else:
            self.page.wait_for_timeout(2000)

    # --- Assertions ---

    def assert_login_success(self) -> None:
        """断言登录成功：URL 已离开 /user/login。"""
        assert "/user/login" not in self.get_current_url()

    def assert_login_error(self) -> None:
        """断言登录失败：仍停留在 /user/login。"""
        assert "/user/login" in self.get_current_url()
