"""零售管理页面对象。

复用采购单据的通用能力（明细行、商品选择、保存、审核、列表查询），
补充零售出库/零售退货特有的会员卡号、收付款、找零和转单操作。
"""

from __future__ import annotations

import allure
from playwright.sync_api import Locator

from infrastructure.pw_core.base_page import BasePage
from utils.logger import get_logger

log = get_logger(__name__)


class RetailPage(BasePage):
    """零售出库、零售退货列表与新增弹窗。"""

    RETAIL_OUT_PATH = "/bill/retail_out"
    RETAIL_BACK_PATH = "/bill/retail_back"

    CLEANUP_PATHS = {
        "retail_out": "/bill/retail_out",
        "retail_back": "/bill/retail_back",
    }

    @allure.step("打开零售出库列表")
    def open_retail_out(self) -> "RetailPage":
        log.info("activating 打开零售出库列表")
        return self._open(self.RETAIL_OUT_PATH)

    @allure.step("打开零售退货列表")
    def open_retail_back(self) -> "RetailPage":
        log.info("activating 打开零售退货列表")
        return self._open(self.RETAIL_BACK_PATH)

    @allure.step("读取列表表头")
    def get_list_headers(self) -> list[str]:
        """返回当前可见列表的表头文本（去掉空白表头）。"""
        log.info("activating 读取列表表头")
        headers = self.page.locator(f"{self.TABLE} thead th")
        values: list[str] = []
        for index in range(headers.count()):
            text = headers.nth(index).inner_text().strip()
            if text:
                values.append(text)
        return values

    @allure.step("读取可见按钮")
    def get_visible_button_labels(self) -> list[str]:
        log.info("activating 读取可见按钮")
        labels: list[str] = []
        buttons = self.page.locator("button:visible")
        for index in range(buttons.count()):
            text = buttons.nth(index).inner_text().strip()
            if text:
                labels.append(text)
        return labels

    @allure.step("读取弹窗文本")
    def get_modal_text(self) -> str:
        log.info("activating 读取弹窗文本")
        return self.get_modal().inner_text()

    @allure.step("选择会员卡号")
    def select_member(self, index: int = 0) -> str:
        log.info("activating 选择会员卡号")
        modal = self.get_modal()
        item = modal.locator(".ant-form-item").filter(has_text="会员卡号").first
        item.locator(".ant-select").first.click()
        self.page.wait_for_timeout(400)
        options = self.page.locator(".ant-select-dropdown:visible li")
        assert options.count() > index, "会员卡号下拉无可用选项"
        text = options.nth(index).inner_text().strip()
        options.nth(index).click()
        self.page.wait_for_timeout(300)
        return text

    @allure.step("读取会员卡号")
    def get_member_text(self) -> str:
        log.info("activating 读取会员卡号")
        item = self.get_modal().locator(".ant-form-item").filter(has_text="会员卡号").first
        return item.locator(".ant-select").first.inner_text().strip()

    @allure.step("读取收款类型")
    def get_payment_type_text(self) -> str:
        log.info("activating 读取收款类型")
        item = self.get_modal().locator(".ant-form-item").filter(has_text="收款类型").first
        return item.locator(".ant-select").first.inner_text().strip()

    @allure.step("读取收款类型选项")
    def get_payment_type_options(self) -> list[str]:
        log.info("activating 读取收款类型选项")
        item = self.get_modal().locator(".ant-form-item").filter(has_text="收款类型").first
        item.locator(".ant-select").first.click()
        self.page.wait_for_timeout(400)
        options = self.page.locator(".ant-select-dropdown:visible li")
        values = [
            options.nth(i).inner_text().strip()
            for i in range(options.count())
            if options.nth(i).inner_text().strip()
        ]
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(200)
        return values

    @allure.step("填写收款/付款金额：{value}")
    def fill_received_amount(self, value: str | int) -> None:
        log.info("activating 填写收款/付款金额")
        self.page.locator("#getAmount:visible").fill(str(value))
        self.page.wait_for_timeout(300)

    @allure.step("读取收款金额")
    def get_received_amount(self) -> str:
        log.info("activating 读取收款金额")
        return self.page.locator("#getAmount:visible").input_value().strip()

    @allure.step("读取找零金额")
    def get_change_amount(self) -> str:
        log.info("activating 读取找零金额")
        return self.page.locator("#backAmount:visible").input_value().strip()

    @allure.step("删除明细行")
    def delete_editable_row(self, index: int = 0) -> None:
        """零售明细行删除：勾选行后工具栏才会出现“移除行”。"""
        log.info("activating 删除明细行")
        self.select_editable_row(index)
        self.click_remove_row()

    @allure.step("零售出库转零售退货：{number}")
    def convert_to_retail_return(self, number: str) -> Locator:
        log.info(f"activating 零售出库转零售退货 {number}")
        self.select_list_row(number)
        assert self._click_visible_text_button(self.page, "转零售退货"), (
            "未找到转零售退货按钮"
        )
        self.page.wait_for_timeout(1200)
        modal = self.get_modal()
        modal.wait_for(state="visible", timeout=10000)
        return modal

    @allure.step("读取关联单号")
    def get_linked_number(self) -> str:
        log.info("activating 读取关联单号")
        return self.page.locator("#linkNumber:visible").input_value().strip()

    @allure.step("读取单据状态")
    def get_status_of_row(self, number: str) -> str:
        log.info("activating 读取单据状态")
        return self.get_status(number)

    @allure.step("判断按钮是否存在")
    def has_button(self, text: str) -> bool:
        log.info("activating 判断按钮是否存在")
        normalized = text.replace(" ", "")
        return any(normalized in label.replace(" ", "") for label in self.get_visible_button_labels())
