"""采购管理页面对象（请购单 / 采购订单 / 采购入库 / 采购退货）。

页面基于 Ant Design Vue，优先使用角色、占位符和可见文本定位；
表格行使用 ``.ant-table`` 结构，但只在可见表格内查找，避免命中隐藏弹窗。
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Iterable

import allure
from playwright.sync_api import Locator, expect

from data_loader import load_config
from pw_core.base_page import BasePage
from utils.logger import get_logger

log = get_logger(__name__)


class PurchasePage(BasePage):
    """采购管理列表页与新增弹窗。"""

    ORDER_PATH = "/bill/purchase_order"
    IN_PATH = "/bill/purchase_in"
    BACK_PATH = "/bill/purchase_back"
    CLEANUP_PATHS = {
        "purchase_apply": "/bill/purchase_apply",
        "purchase_order": "/bill/purchase_order",
        "purchase_in": "/bill/purchase_in",
        "purchase_back": "/bill/purchase_back",
        "retail_out": "/bill/retail_out",
        "retail_back": "/bill/retail_back",
    }

    # 只在可见表格内定位，避免命中详情弹窗里的隐藏表格
    TABLE = ".ant-table:visible"
    MODAL = ".ant-modal:visible"
    GUIDE_ROOT = ".introjs-overlay, .introjs-tooltip"

    def __init__(self) -> None:
        super().__init__()
        self.base_url = load_config().base_url.rstrip("/")
        self.last_product_stock = ""

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------

    def _wait_for_visible_text(
        self,
        locator: Locator,
        text: str,
        timeout: int = 5000,
    ) -> Locator:
        """轮询等待包含指定文本的元素可见。"""
        deadline = time.time() + timeout / 1000
        while time.time() < deadline:
            for index in range(locator.count()):
                item = locator.nth(index)
                try:
                    if item.is_visible() and text in item.inner_text():
                        return item
                except Exception:
                    continue
            self.page.wait_for_timeout(200)
        raise AssertionError(f"等待提示文本超时：{text}")

    # ------------------------------------------------------------------
    # 打开页面
    # ------------------------------------------------------------------

    @allure.step("打开采购订单列表")
    def open_purchase_order(self) -> "PurchasePage":
        return self._open(self.ORDER_PATH)

    @allure.step("打开采购入库列表")
    def open_purchase_in(self) -> "PurchasePage":
        return self._open(self.IN_PATH)

    @allure.step("打开采购退货列表")
    def open_purchase_back(self) -> "PurchasePage":
        return self._open(self.BACK_PATH)

    # ------------------------------------------------------------------
    # 列表断言与查询
    # ------------------------------------------------------------------

    @allure.step("断言采购页面已加载：{module_name}")
    def assert_page_loaded(self, module_name: str, url_fragment: str) -> None:
        self.dismiss_guide()
        self.assert_url_contains(url_fragment)
        self.assert_visible(self.TABLE, f"{module_name}列表")
        self._first_visible(
            self.page.get_by_role("button", name=re.compile(r"新\s*增"))
        )

    @allure.step("等待列表渲染完成")
    def wait_for_list_ready(self, timeout: int = 15000) -> int:
        """等待表格出现数据行或空状态，返回数据行数。"""
        deadline = time.time() + timeout / 1000
        empty_locator = self.page.locator(
            ".ant-empty-description, .ant-empty, .ant-table-placeholder"
        )
        while time.time() < deadline:
            count = self.get_row_count()
            if count > 0:
                return count
            for index in range(empty_locator.count()):
                if empty_locator.nth(index).is_visible():
                    return 0
            self.page.wait_for_timeout(200)
        return self.get_row_count()

    @allure.step("读取列表第一行单据编号")
    def get_first_row_number(self) -> str:
        rows = self.get_rows()
        if rows.count() == 0:
            raise AssertionError("采购列表为空，无法读取第一行单据编号")

        row = rows.first
        cells = row.locator("td")
        for index in range(cells.count()):
            text = cells.nth(index).inner_text().strip()
            match = re.search(r"(CGDD|CGRK|CGTH|QGD|LSCK|LSTH)\d{5,}", text)
            if match:
                # 单元格可能带 [请]/[转] 后缀，查询时只取纯单号
                return match.group(0)

        match = re.search(r"(CGDD|CGRK|CGTH|QGD|LSCK|LSTH)\d{5,}", row.inner_text())
        if match:
            return match.group(0)
        raise AssertionError(f"第一行未找到单据编号：{row.inner_text()[:200]}")

    @allure.step("断言列表为空状态")
    def assert_empty_state(self) -> None:
        """列表默认按近 3 天过滤，无数据时应显示 Ant Design 空状态。"""
        empty = self.page.locator(
            ".ant-empty-description, .ant-empty, .ant-table-placeholder"
        ).filter(visible=True).first
        expect(empty).to_be_visible(timeout=10000)

    @allure.step("断言列表存在单据：{number}")
    def assert_row_exists(self, number: str) -> None:
        row = self.get_rows().filter(has_text=number).first
        expect(row).to_be_visible(timeout=10000)

    # ------------------------------------------------------------------
    # 新增弹窗
    # ------------------------------------------------------------------

    @allure.step("断言新增弹窗标题与字段")
    def assert_add_modal_visible(
        self,
        module_name: str,
        labels: Iterable[str],
    ) -> None:
        self.dismiss_guide()
        modal = self.get_modal()
        title = modal.locator(self.MODAL_TITLE).inner_text().strip()
        assert "新增" in title, f"{module_name}新增弹窗标题异常：{title}"

        modal_text = modal.inner_text()
        missing = [label for label in labels if label not in modal_text]
        assert not missing, (
            f"新增弹窗缺少字段：{missing}；"
            f"弹窗文本片段：{modal_text[:300]}"
        )

    @allure.step("断言新增弹窗包含商品明细表")
    def assert_material_table_visible(self) -> None:
        self.dismiss_guide()
        modal = self.get_modal()
        # 采购单据明细使用自定义 JEditableTable（#billModal / .input-table），
        # 不是 Ant Design 的标准 a-table。
        table = modal.locator("#billModal:visible, .input-table:visible").first
        expect(table).to_be_visible(timeout=10000)

    @allure.step("点击保存")
    def click_save(self) -> None:
        self.dismiss_guide()
        modal = self.get_modal()
        try:
            button = self._first_visible(
                modal.get_by_role("button", name=re.compile(r"保\s*存.*Ctrl"))
            )
        except AssertionError:
            button = self._first_visible(
                modal.get_by_role("button", name=re.compile(r"^保\s*存$"))
            )
        button.click()

    @allure.step("断言弹窗仍然可见")
    def assert_modal_visible(self) -> None:
        expect(self.get_modal()).to_be_visible(timeout=5000)

    @allure.step("断言出现校验提示：{text}")
    def assert_validation_message(self, text: str) -> None:
        locator = self.page.locator(f"{self.MESSAGE}, {self.FORM_ERROR}")
        self._wait_for_visible_text(locator, text, timeout=5000)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    @allure.step("断言列表状态标签都在允许范围内")
    def assert_status_tags(self, allowed: set[str] | list[str]) -> None:
        allowed = set(allowed)
        texts = self.get_status_tag_texts()
        assert texts, "采购列表未显示任何状态标签"
        unknown = sorted(set(texts) - allowed)
        assert not unknown, (
            f"出现未识别的状态标签：{unknown}；"
            f"允许值：{sorted(allowed)}；实际值：{sorted(set(texts))}"
        )

    # ------------------------------------------------------------------
    # 采购单据通用流程操作
    # ------------------------------------------------------------------

    APPLY_PATH = "/bill/purchase_apply"
    PAYMENT_PATH = "/financial/money_out"

    @allure.step("打开请购单列表")
    def open_purchase_apply(self) -> "PurchasePage":
        return self._open(self.APPLY_PATH)

    @allure.step("打开付款单列表")
    def open_payment_list(self) -> "PurchasePage":
        return self._open(self.PAYMENT_PATH)

    @allure.step("读取付款单号")
    def get_payment_number(self, timeout: int = 15000) -> str:
        log.info("activating 读取付款单号")
        """付款单新增弹窗的单据编号使用 #billNo。"""
        deadline = time.time() + timeout / 1000
        while time.time() < deadline:
            try:
                value = self.page.locator("#billNo:visible").input_value().strip()
            except Exception:
                value = ""
            if value:
                return value
            self.page.wait_for_timeout(200)
        return ""

    @allure.step("读取商品库存")
    def get_last_product_stock(self) -> str:
        log.info("activating 读取商品库存")
        return self.last_product_stock

    @allure.step("填写本次退款/付款金额")
    def fill_change_amount(self, value: str | int) -> None:
        log.info("activating 填写本次退款/付款金额")
        log.info(f"activating 填写本次退款/付款 {value}")
        self.page.locator("#changeAmount:visible").fill(str(value))

    @allure.step("读取欠款金额")
    def get_debt_value(self) -> str:
        log.info("activating 读取欠款金额")
        return self.page.locator("#debt:visible").input_value().strip()

    @allure.step("选择供应商")
    def select_supplier(self, name: str) -> None:
        log.info("activating 选择供应商")
        log.info(f"activating 选择供应商 {name}")
        modal = self.get_modal()
        item = modal.locator(".ant-form-item").filter(has_text="供应商").first
        item.locator(".ant-select").first.click()
        self.page.wait_for_timeout(400)
        dropdown = self.page.locator(".ant-select-dropdown:visible").last
        search = dropdown.locator("input.ant-select-search__field:visible")
        if search.count():
            search.fill(name)
            self.page.wait_for_timeout(600)
        option = self.page.locator(".ant-select-dropdown:visible li").filter(has_text=name).first
        option.click()
        self.page.wait_for_timeout(300)

    @allure.step("选择结算账户")
    def select_account(self, name: str) -> None:
        log.info("activating 选择结算账户")
        modal = self.get_modal()
        item = modal.locator(".ant-form-item").filter(has_text="结算账户").first
        item.locator(".ant-select").first.click()
        self.page.wait_for_timeout(400)
        self.page.locator(".ant-select-dropdown:visible li").filter(has_text=name).first.click()
        self.page.wait_for_timeout(300)

    @allure.step("选择仓库")
    def select_warehouse(self, name: str | None = None, row_index: int = 0) -> None:
        log.info("activating 选择仓库")
        log.info(f"activating 选择仓库 {name or '第一个可用仓库'} row={row_index}")
        modal = self.get_modal()
        # 订单页没有仓库字段；入库/退货的仓库在明细行
        form_item = modal.locator(".ant-form-item").filter(has_text="仓库").first
        if form_item.count():
            target = form_item.locator(".ant-select").first
        else:
            target = self.get_editable_rows().nth(row_index).locator(".ant-select").first
        target.click(force=True)
        self.page.wait_for_timeout(400)
        self.page.locator(".ant-select-dropdown:visible").last.wait_for(
            state="visible", timeout=5000
        )
        options = self.page.locator(".ant-select-dropdown:visible li")
        assert options.count() > 0, "仓库下拉无选项"
        if name:
            options.filter(has_text=name).first.click()
        else:
            # 跳过禁用项（"需在商品管理先新增才能使用"）
            for index in range(options.count()):
                item = options.nth(index)
                if item.get_attribute("aria-disabled") != "true":
                    item.click()
                    break
            else:
                raise AssertionError("仓库下拉无可用选项")
        self.page.wait_for_timeout(300)

    @allure.step("判断是否已关联单据")
    def has_link_data(self, input_id: str, number: str) -> bool:
        log.info("activating 判断是否已关联单据")
        """打开关联弹窗搜索单号，返回是否有数据；用于 010/018 反例。"""
        modal = self.get_modal()
        modal.locator(f"#{input_id}").locator(
            "xpath=..//i[contains(@class,'anticon-search')]"
        ).first.click()
        self.page.wait_for_timeout(800)
        link_modal = self.page.locator(".ant-modal:visible").last
        search = link_modal.locator("input[placeholder*='单据编号']:visible").first
        search.fill(number)
        if not self._click_visible_text_button(link_modal, "查"):
            return False
        self.page.wait_for_timeout(900)
        count = link_modal.locator(".ant-table-tbody tr.ant-table-row:visible").count()
        # 关闭关联弹窗
        if not self._click_visible_text_button(link_modal, "取 消"):
            self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(400)
        return count > 0

    def get_visible_error_count(self) -> int:
        return self.page.locator(
            ".has-error:visible, .ant-form-item-has-error:visible, "
            ".ant-form-explain:visible, .ant-form-item-explain:visible"
        ).count()

    @allure.step("转采购入库：{number}")
    def transfer_to_inbound(self, number: str) -> Locator:
        log.info(f"activating 转采购入库 {number}")
        self.select_list_row(number)
        clicked = False
        for index in range(self.page.locator("button:visible").count()):
            button = self.page.locator("button:visible").nth(index)
            label = button.inner_text().strip()
            if "转" in label and "入库" in label:
                button.click()
                clicked = True
                break
        assert clicked, "未找到转采购入库按钮"
        self.page.wait_for_timeout(1200)
        modal = self.get_modal()
        modal.wait_for(state="visible", timeout=10000)
        return modal

    @allure.step("付款单选择采购欠款单据：{number}")
    def choose_payment_bill(self, number: str) -> None:
        log.info(f"activating 付款单选择单据 {number}")
        modal = self.get_modal()
        clicked = False
        for index in range(modal.locator("button:visible").count()):
            button = modal.locator("button:visible").nth(index)
            if "选择单据" in button.inner_text():
                button.click()
                clicked = True
                break
        assert clicked, "付款单未找到选择单据按钮"
        self.page.wait_for_timeout(1000)

        selector = self.page.locator(".ant-modal:visible").last
        search = selector.locator("input[placeholder*='单据编号查询']:visible").first
        search.fill(number)
        assert self._click_visible_text_button(selector, "查"), "付款单选择弹窗未找到查询按钮"
        self.page.wait_for_timeout(900)

        rows = selector.locator(".ant-table-tbody tr.ant-table-row:visible")
        target = rows.filter(has_text=number).first
        assert target.count() > 0, f"付款单选择弹窗无目标单据：{number}"
        checkbox = target.locator("input[type=checkbox], input[type=radio]")
        if checkbox.count():
            checkbox.first.check()
        else:
            target.click()
        footer = selector.locator(".ant-modal-footer")
        footer.get_by_role("button", name=re.compile(r"确\s*定")).click()
        self.page.wait_for_timeout(1000)

    @allure.step("付款单填写本次付款：{value}")
    def fill_payment_amount(self, value: str | int) -> None:
        log.info(f"activating 付款单填写本次付款 {value}")
        locator = self.page.locator(
            "input[placeholder*='本次付款']:visible, input[id^='changeAmount_']:visible"
        ).first
        locator.fill(str(value))
        self.page.wait_for_timeout(300)

    # ------------------------------------------------------------------
    # 类级测试数据清理
    # ------------------------------------------------------------------

    def _search_panel(self) -> Locator:
        return self.page.locator(".table-page-search-wrapper:visible").first

    def _status_form_item(self) -> Locator:
        panel = self._search_panel()
        for label in ("单据状态", "状态"):
            item = panel.locator(".ant-form-item").filter(has_text=label)
            if item.count():
                return item.first
        return panel.locator(".ant-form-item").filter(has_text="___not_found___")

    # ------------------------------------------------------------------
    # 测试数据准备 / 清理（fixture 专用）
    # ------------------------------------------------------------------

    @allure.step("创建请购单：{product} x {quantity}")
    def create_requisition(
        self, product: str, quantity: int | str = 1, audit: bool = False
    ) -> str:
        self.open_purchase_apply()
        self.open_add_modal()
        number = self.get_form_number()
        self.open_product_selector(0)
        self.choose_product(product, mode="dblclick")
        self.fill_editable_quantity(quantity)
        if audit:
            self.click_save_and_audit()
        else:
            self.click_save_only()
        self.wait_modal_closed()
        if audit:
            self.wait_for_status(number, "已审核")
        return number

    @allure.step("创建采购订单：{product} x {quantity}")
    def create_order(
        self,
        supplier: str,
        product: str,
        quantity: int | str = 1,
        price: float | str = 0.5,
        account: str = "账户1",
    ) -> str:
        self.open_purchase_order()
        self.open_add_modal()
        number = self.get_form_number()
        self.select_supplier(supplier)
        self.open_product_selector(0)
        self.choose_product(product, mode="dblclick")
        self.fill_editable_quantity(quantity)
        self.fill_editable_price(price)
        self.select_account(account)
        self.click_save_only()
        self.wait_modal_closed()
        return number

    @allure.step("创建采购入库单：{product} x {quantity}")
    def create_inbound(
        self,
        supplier: str,
        product: str,
        quantity: int | str = 1,
        price: float | str = 0.5,
        account: str = "账户1",
        audit: bool = True,
    ) -> str:
        self.open_purchase_in()
        self.open_add_modal()
        number = self.get_form_number()
        self.select_supplier(supplier)
        self.select_warehouse()
        self.open_product_selector(0)
        self.choose_product(product, mode="dblclick")
        self.fill_editable_quantity(quantity)
        self.fill_editable_price(price)
        self.select_account(account)
        if audit:
            self.click_save_and_audit()
        else:
            self.click_save_only()
        self.wait_modal_closed()
        if audit:
            self.wait_for_status(number, "已审核")
        return number

    @allure.step("创建采购退货单：{product} x {quantity}")
    def create_return(
        self,
        supplier: str,
        product: str,
        quantity: int | str = 1,
        price: float | str = 0.5,
        account: str = "账户1",
        audit: bool = False,
    ) -> str:
        self.open_purchase_back()
        self.open_add_modal()
        number = self.get_form_number()
        self.select_supplier(supplier)
        self.select_warehouse()
        self.open_product_selector(0)
        self.choose_product(product, mode="dblclick")
        self.fill_editable_quantity(quantity)
        self.fill_editable_price(price)
        self.select_account(account)
        if audit:
            self.click_save_and_audit()
        else:
            self.click_save_only()
        self.wait_modal_closed()
        if audit:
            self.wait_for_status(number, "已审核")
        return number

    @allure.step("转采购退货：{number}")
    def transfer_to_return(self, number: str) -> None:
        """勾选入库单，点列表上方工具栏的「转采购退货」按钮（与「新增」同一层）。"""
        self.select_list_row(number)
        self.page.get_by_role("button", name="转采购退货").first.click()
        self.page.wait_for_timeout(800)

    @allure.step("等待单据状态：{number} -> {expected}")
    def wait_for_status(self, number: str, expected: str, timeout: int = 15000) -> None:
        """轮询列表状态，直到等于 expected 或超时。"""
        deadline = time.time() + timeout / 1000
        last = ""
        while time.time() < deadline:
            try:
                last = self.get_status(number)
            except Exception:
                last = ""
            if last == expected:
                return
            self.page.wait_for_timeout(500)
        raise AssertionError(f"单据 {number} 状态未变为 {expected}，当前 {last}")

    @allure.step("删除单据：{number}")
    def delete_bill(self, number: str, module: str = "purchase_apply") -> None:
        """按单号反审核并删除；单据不存在时静默跳过。"""
        try:
            self.open_cleanup_module(module)
            row = self.get_list_row(number)
            if row.count() == 0:
                return
            status = self.get_status(number)
            if status in {"已审核", "完成出库", "完成采购"}:
                self.click_row_action(number, "反审核")
                self.confirm_popup()
                self.page.wait_for_timeout(500)
            self.click_row_action(number, "删除")
            self.confirm_popup()
            self.page.wait_for_timeout(500)
        except Exception:
            pass

