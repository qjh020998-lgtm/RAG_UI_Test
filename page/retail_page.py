"""零售管理页面对象。

复用采购单据的通用能力（明细行、商品选择、保存、审核、列表查询），
补充零售出库/零售退货特有的会员卡号、收付款、找零和转单操作。
"""

from __future__ import annotations

import allure
from playwright.sync_api import Locator

from page.purchase_page import PurchasePage
from utils.logger import get_logger

log = get_logger(__name__)


class RetailPage(PurchasePage):
    """零售出库、零售退货列表与新增弹窗。"""

    RETAIL_OUT_PATH = "/bill/retail_out"
    RETAIL_BACK_PATH = "/bill/retail_back"

    @allure.step("打开零售出库列表")
    def open_retail_out(self) -> "RetailPage":
        return self._open(self.RETAIL_OUT_PATH)

    @allure.step("打开零售退货列表")
    def open_retail_back(self) -> "RetailPage":
        return self._open(self.RETAIL_BACK_PATH)

    def get_list_headers(self) -> list[str]:
        """返回当前可见列表的表头文本（去掉空白表头）。"""
        headers = self.page.locator(f"{self.TABLE} thead th")
        values: list[str] = []
        for index in range(headers.count()):
            text = headers.nth(index).inner_text().strip()
            if text:
                values.append(text)
        return values

    def get_visible_button_labels(self) -> list[str]:
        labels: list[str] = []
        buttons = self.page.locator("button:visible")
        for index in range(buttons.count()):
            text = buttons.nth(index).inner_text().strip()
            if text:
                labels.append(text)
        return labels

    def get_modal_text(self) -> str:
        return self.get_modal().inner_text()

    @allure.step("选择会员卡号")
    def select_member(self, index: int = 0) -> str:
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

    def get_member_text(self) -> str:
        item = self.get_modal().locator(".ant-form-item").filter(has_text="会员卡号").first
        return item.locator(".ant-select").first.inner_text().strip()

    def get_payment_type_text(self) -> str:
        item = self.get_modal().locator(".ant-form-item").filter(has_text="收款类型").first
        return item.locator(".ant-select").first.inner_text().strip()

    def get_payment_type_options(self) -> list[str]:
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
        self.page.locator("#getAmount:visible").fill(str(value))
        self.page.wait_for_timeout(300)

    def get_received_amount(self) -> str:
        return self.page.locator("#getAmount:visible").input_value().strip()

    def get_change_amount(self) -> str:
        return self.page.locator("#backAmount:visible").input_value().strip()

    @allure.step("删除明细行")
    def delete_editable_row(self, index: int = 0) -> None:
        """零售明细行删除：勾选行后工具栏才会出现“移除行”。"""
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

    def get_linked_number(self) -> str:
        return self.page.locator("#linkNumber:visible").input_value().strip()

    def get_status_of_row(self, number: str) -> str:
        return self.get_status(number)

    def has_button(self, text: str) -> bool:
        normalized = text.replace(" ", "")
        return any(normalized in label.replace(" ", "") for label in self.get_visible_button_labels())
