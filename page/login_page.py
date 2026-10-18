"""Login Page Object."""

import re

from playwright.sync_api import Page, expect
from page.base_page import BasePage


class LoginPage(BasePage):
    """登录页面的 Page Object。

    针对 http://39.101.122.147:3000/user/login 的 ERP 登录页面。
    """

    def __init__(self, page: Page, base_url: str = ""):
        super().__init__(page)
        self.base_url = base_url

    # ========== 元素定位 ==========

    @property
    def username_input(self):
        """用户名输入框"""
        return self.page.locator("#loginName")

    @property
    def password_input(self):
        """密码输入框"""
        return self.page.locator("#password")

    @property
    def login_button(self):
        """登录按钮"""
        return self.page.locator(".login-button")

    @property
    def remember_checkbox(self):
        """记住密码复选框"""
        return self.page.locator(".ant-checkbox-input")

    # ========== 业务操作 ==========

    def goto_login(self) -> None:
        """导航到登录页面。"""
        login_url = f"{self.base_url}/user/login"
        self.navigate(login_url)
        self.wait_for_load()

    def login(self, username: str, password: str) -> None:
        """执行登录操作。

        Args:
            username: 用户名
            password: 密码
        """
        self.actions.fill(self.username_input, username)
        self.actions.fill(self.password_input, password)
        self.actions.click(self.login_button)

    def login_and_wait(
        self,
        username: str,
        password: str,
        expect_success: bool = True,
        timeout: int = 15000,
    ) -> None:
        """登录并等待结果。

        Args:
            username: 用户名
            password: 密码
            expect_success: 期望登录成功并离开登录页
            timeout: 等待超时（毫秒）
        """
        self.login(username, password)

        if expect_success:
            # 登录成功后应跳转离开登录页（具体落地页由后端决定）
            expect(self.page).not_to_have_url(
                re.compile(r".*/user/login/?(?:\?.*)?$"),
                timeout=timeout,
            )
        else:
            self.page.wait_for_timeout(2000)

    def assert_login_success(self) -> None:
        """断言登录成功：URL 不再停留在 /user/login。"""
        current_url = self.get_url()
        assert "/user/login" not in current_url, \
            f"登录失败，仍在登录页面: {current_url}"

    def assert_login_error(self) -> None:
        """断言登录失败：仍在登录页面。"""
        current_url = self.get_url()
        assert "/user/login" in current_url, \
            f"意外跳转到其他页面: {current_url}"

    def toggle_remember(self) -> None:
        """切换记住密码复选框。"""
        self.actions.click(self.remember_checkbox)

    def is_remember_checked(self) -> bool:
        """检查记住密码是否勾选。"""
        return self.remember_checkbox.is_checked()
