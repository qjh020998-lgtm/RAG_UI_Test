"""BaseTest 与 BasePage 契约检查。

``BasePageContract`` 是 BasePage 的护栏：如果未来改动删除了定位、动作、
Toast、弹窗、表格等基础设施方法，自测会在业务套件变红之前失败。
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from pw_core.base_page import BasePage


class BasePageContract:
    """校验 ``BasePage`` 是否仍满足 Phase 3.0 的契约。"""

    REQUIRED_METHODS = (
        "navigate",
        "open",
        "get_url",
        "get_title",
        "wait_for_load",
        "wait_for_page_ready",
        "by_role",
        "by_text",
        "by_label",
        "by_placeholder",
        "by_test_id",
        "click",
        "fill",
        "select_option",
        "get_text",
        "get_input_value",
        "wait_for_visible",
        "assert_visible",
        "assert_text",
        "wait_for_toast",
        "assert_toast",
        "wait_for_dialog",
        "assert_dialog_visible",
        "handle_unsaved_changes",
        "get_table",
        "get_table_rows",
        "assert_url_contains",
        "screenshot_on_failure",
    )

    @classmethod
    def errors(cls, base_page_cls: type = BasePage) -> list[str]:
        """返回契约违规列表；空列表表示通过。"""
        problems: list[str] = []

        for name in cls.REQUIRED_METHODS:
            member = getattr(base_page_cls, name, None)
            if member is None:
                problems.append(f"缺少方法: {name}")
            elif not callable(member):
                problems.append(f"不是可调用对象: {name}")

        for name in ("TOAST_SELECTOR", "DIALOG_SELECTOR", "TABLE_SELECTOR"):
            if not hasattr(base_page_cls, name):
                problems.append(f"缺少选择器: {name}")

        return problems

    @classmethod
    def assert_valid(cls, base_page_cls: type = BasePage) -> None:
        """存在违规时抛出 ``AssertionError``，并列出全部问题。"""
        problems = cls.errors(base_page_cls)
        assert not problems, "BasePage 契约违规:\n" + "\n".join(
            f"  - {item}" for item in problems
        )


class BaseTest:
    """把 Playwright Page 包装成 BasePage 的可选辅助类。"""

    def __init__(
        self,
        page: Page,
        base_url: str = "",
        config: Any | None = None,
    ):
        self.page = page
        self.base_page = BasePage(page, base_url=base_url, config=config)

    def assert_base_page_contract(self) -> None:
        """BasePage 缺少基础设施方法时失败。"""
        BasePageContract.assert_valid(type(self.base_page))

    def assert_page_ready(self, selector: str | None = None) -> None:
        """委托 ``BasePage.wait_for_page_ready``。"""
        self.base_page.wait_for_page_ready(selector=selector)
