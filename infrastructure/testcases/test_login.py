"""登录测试用例（数据驱动，参数统一通过 parametrize 传入）。"""

import pytest

from data_loader import load_login_cases
from infrastructure.page.login_page import LoginPage

# 测试数据来自 data/login_cases.yaml，模块加载时完成校验
LOGIN_CASES = load_login_cases()


@pytest.mark.smoke
@pytest.mark.no_auth
class TestLogin:
    """登录测试用例：必须从登出状态开始，不复用 storage_state。"""

    @pytest.mark.parametrize(
        "case",
        LOGIN_CASES,
        ids=[case.name for case in LOGIN_CASES],
    )
    def test_login(self, login_page: LoginPage, case) -> None:
        """使用 YAML 中的账号密码验证登录结果。"""
        # Arrange
        login_page.goto_login()

        # Act
        login_page.login_and_wait(
            case.username,
            case.password,
            expect_success=case.expect_success,
        )

        # Assert
        if case.expect_success:
            login_page.assert_login_success()
        else:
            login_page.assert_login_error()
