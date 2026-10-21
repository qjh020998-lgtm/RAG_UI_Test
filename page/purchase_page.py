"""采购管理页面对象（请购单 / 采购订单 / 采购入库 / 采购退货）。

页面基于 Ant Design Vue，优先使用角色、占位符和可见文本定位；
表格行使用 ``.ant-table`` 结构，但只在可见表格内查找，避免命中隐藏弹窗。
"""

from __future__ import annotations

import re
import time
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

    # 只在可见表格内定位，避免命中详情弹窗里的隐藏表格
    TABLE = ".ant-table:visible"
    TABLE_ROWS = ".ant-table:visible .ant-table-tbody tr.ant-table-row"
    MODAL = ".ant-modal:visible"
    MODAL_TITLE = ".ant-modal-title"
    MESSAGE = ".ant-message-notice-content"
    FORM_ERROR = ".ant-form-explain, .ant-form-item-explain"
    GUIDE_ROOT = ".introjs-overlay, .introjs-tooltip"

    def __init__(self) -> None:
        super().__init__()
        self.base_url = load_config().base_url.rstrip("/")

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------

    @staticmethod
    def _first_visible(locator: Locator) -> Locator:
        """返回 locator 中第一个可见元素；不存在时抛 AssertionError。"""
        for index in range(locator.count()):
            item = locator.nth(index)
            if item.is_visible():
                return item
        raise AssertionError(f"没有找到可见元素：{locator}")

    def _click_button(self, name: re.Pattern[str]) -> None:
        """点击第一个可见按钮。"""
        button = self._first_visible(self.page.get_by_role("button", name=name))
        button.click()

    def _click_text(self, text: str | re.Pattern[str]) -> None:
        """点击第一个可见文本（用于 Ant Design 的 a 标签按钮）。"""
        if isinstance(text, str):
            locator = self.page.get_by_text(text, exact=True)
        else:
            locator = self.page.get_by_text(text)
        self._first_visible(locator).click()

    @allure.step("关闭新手引导遮罩")
    def dismiss_guide(self) -> None:
        """关闭采购订单/采购入库首次打开时自动弹出的 intro.js 引导。"""
        guide = self.page.locator(self.GUIDE_ROOT)
        if guide.count() == 0:
            self.page.wait_for_timeout(300)
        if guide.count() == 0:
            return

        # intro.js 的“跳过/关闭”按钮
        for selector in (
            ".introjs-skipbutton",
            ".introjs-donebutton",
            ".introjs-tooltip .introjs-closebutton",
            ".introjs-tooltip [class*='close']",
        ):
            buttons = self.page.locator(selector)
            for index in range(buttons.count()):
                item = buttons.nth(index)
                try:
                    if item.is_visible():
                        item.click(force=True)
                        self.page.wait_for_timeout(300)
                        break
                except Exception:
                    continue
            if guide.count() == 0:
                return

        # 键盘 Esc 退出
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(300)
        if guide.count() == 0:
            return

        # 最后兜底：调用 introJs().exit() 并清理残留遮罩/aria-hidden
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
        self.page.wait_for_timeout(200)

    def _wait_table_idle(self, timeout: int = 15000) -> None:
        """等待表格 loading 消失；没有 loading 时立即返回。"""
        spinner = self.page.locator(".ant-spin-spinning")
        try:
            spinner.first.wait_for(state="hidden", timeout=timeout)
        except Exception:
            pass

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

    def _open(self, path: str) -> "PurchasePage":
        log.info(f"activating 打开采购列表 {path}")
        last_error = None
        for attempt in range(2):
            self.page.goto(f"{self.base_url}{path}", wait_until="domcontentloaded")
            self.wait_for_load()
            try:
                self.wait_for_visible(self.TABLE, timeout=60000)
                break
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    self.page.wait_for_timeout(1500)
                    continue
                raise
        if last_error and self.page.locator(self.TABLE).count() == 0:
            raise last_error
        self._wait_table_idle()
        self.dismiss_guide()
        return self

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

    def get_rows(self) -> Locator:
        return self.page.locator(self.TABLE_ROWS)

    def get_row_count(self) -> int:
        return self.get_rows().count()

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
            match = re.search(r"(CGDD|CGRK|CGTH|QGD)\d{5,}", text)
            if match:
                # 单元格可能带 [请]/[转] 后缀，查询时只取纯单号
                return match.group(0)

        match = re.search(r"(CGDD|CGRK|CGTH|QGD)\d{5,}", row.inner_text())
        if match:
            return match.group(0)
        raise AssertionError(f"第一行未找到单据编号：{row.inner_text()[:200]}")

    @allure.step("按单据编号查询：{number}")
    def search_by_number(self, number: str) -> None:
        self.dismiss_guide()
        try:
            input_box = self._first_visible(
                self.page.get_by_placeholder(re.compile("请输入单据编号"))
            )
        except AssertionError:
            self._click_text(re.compile(r"展\s*开"))
            input_box = self._first_visible(
                self.page.get_by_placeholder(re.compile("请输入单据编号"))
            )

        input_box.fill("")
        input_box.fill(number)
        self.dismiss_guide()
        self._click_button(re.compile(r"查\s*询"))
        self._wait_table_idle()

    @allure.step("重置查询条件")
    def reset_search(self) -> None:
        self.dismiss_guide()
        self._click_button(re.compile(r"重\s*置"))
        self._wait_table_idle()

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

    @allure.step("打开新增弹窗")
    def open_add_modal(self) -> Locator:
        log.info("activating 点击新增")
        self.dismiss_guide()
        self._click_button(re.compile(r"新\s*增"))
        modal = self.get_modal()
        modal.wait_for(state="visible", timeout=15000)
        modal.locator(".ant-form").first.wait_for(state="visible", timeout=10000)
        self.dismiss_guide()
        return modal

    def get_modal(self) -> Locator:
        return self.page.locator(self.MODAL).last

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

    @allure.step("关闭新增弹窗")
    def close_modal(self) -> None:
        self.dismiss_guide()
        modal = self.get_modal()
        try:
            button = self._first_visible(
                modal.get_by_role("button", name=re.compile(r"取\s*消"))
            )
        except AssertionError:
            button = self._first_visible(
                modal.get_by_role("button", name=re.compile(r"关\s*闭"))
            )
        button.click()
        self.page.wait_for_timeout(400)
        # 有未保存明细时可能弹出“是否离开”确认
        if self.page.locator(".ant-modal-confirm:visible").count():
            self._click_visible_text_button(self.page, "确 定") or self._click_visible_text_button(
                self.page, "确定"
            )
        self.wait_modal_closed(timeout=8000)

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

    @allure.step("读取列表状态标签")
    def get_status_tag_texts(self) -> list[str]:
        tags = self.page.locator(f"{self.TABLE_ROWS} .ant-tag")
        texts: list[str] = []
        for index in range(tags.count()):
            item = tags.nth(index)
            if item.is_visible():
                text = item.inner_text().strip()
                if text:
                    texts.append(text)
        return texts

    @allure.step("断言列表状态标签都在允许范围内")
    def assert_status_tags(self, allowed: set[str]) -> None:
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

    @staticmethod
    def _click_visible_text_button(
        scope: Locator,
        text: str,
        exclude: str | None = None,
    ) -> bool:
        """按按钮 inner_text 点击（兼容 Ant Design 插入空格）。"""
        for index in range(scope.locator("button:visible").count()):
            button = scope.locator("button:visible").nth(index)
            try:
                label = button.inner_text().strip()
            except Exception:
                continue
            if text in label and (exclude is None or exclude not in label):
                button.click()
                return True
        return False

    def get_form_number(self, timeout: int = 15000) -> str:
        """等待新增弹窗自动生成的 #number 回填后再返回。"""
        deadline = time.time() + timeout / 1000
        while time.time() < deadline:
            try:
                value = self.page.locator("#number:visible").input_value().strip()
            except Exception:
                value = ""
            if value:
                return value
            self.page.wait_for_timeout(200)
        return ""

    def get_payment_number(self, timeout: int = 15000) -> str:
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

    def get_editable_rows(self) -> Locator:
        return self.get_modal().locator(".tbody .tr[id*='tbody-tr']:visible")

    def get_editable_row_count(self) -> int:
        return self.get_editable_rows().count()

    @allure.step("新增明细行")
    def click_insert_row(self) -> None:
        log.info("activating 插入行")
        assert self._click_visible_text_button(self.get_modal(), "插入行"), "未找到插入行按钮"
        self.page.wait_for_timeout(400)

    @allure.step("选择明细行复选框")
    def select_editable_row(self, index: int = 0) -> None:
        row = self.get_editable_rows().nth(index)
        row.locator("input[type=checkbox]").first.check()

    @allure.step("移除明细行")
    def click_remove_row(self) -> None:
        log.info("activating 移除行")
        assert self._click_visible_text_button(self.get_modal(), "移除行"), "未找到移除行按钮"
        self.page.wait_for_timeout(300)
        self.confirm_popup()

    @allure.step("确认二次弹窗")
    def confirm_popup(self) -> None:
        for scope in (
            self.page.locator(".ant-modal-confirm:visible"),
            self.page.locator(".ant-popover:visible"),
            self.page.locator(".ant-modal:visible"),
        ):
            if scope.count() == 0:
                continue
            if self._click_visible_text_button(scope.last, "确 定") or self._click_visible_text_button(scope.last, "确定"):
                self.page.wait_for_timeout(300)
                return
        # 兜底：全局可见按钮
        assert (
            self._click_visible_text_button(self.page, "确 定")
            or self._click_visible_text_button(self.page, "确定")
        ), "未找到确认按钮"

    @allure.step("打开商品选择弹窗")
    def open_product_selector(self, row_index: int = 0) -> Locator:
        log.info(f"activating 打开商品选择 row={row_index}")
        row = self.get_editable_rows().nth(row_index)
        row.locator("button.ant-btn-icon-only").first.click()
        self.page.wait_for_timeout(700)

        # 主单据弹窗和商品弹窗会同时存在，商品弹窗是最后一个可见 modal
        for _ in range(10):
            modals = self.page.locator(".ant-modal:visible")
            if modals.count() >= 1:
                product = modals.last
                try:
                    title = product.locator(".ant-modal-title").inner_text().strip()
                except Exception:
                    title = ""
                if "选择商品" in title:
                    return product
            self.page.wait_for_timeout(300)
        raise AssertionError("商品选择弹窗未打开")

    @allure.step("搜索并选择商品：{keyword}")
    def choose_product(self, keyword: str, mode: str = "dblclick") -> None:
        log.info(f"activating 选择商品 {keyword} mode={mode}")
        modals = self.page.locator(".ant-modal:visible")
        product = modals.last
        assert "选择商品" in product.locator(".ant-modal-title").inner_text(), (
            "当前不是商品选择弹窗"
        )
        search = product.locator(
            "input[placeholder*='条码']:visible, input[placeholder*='关键词']:visible"
        ).first
        rows = product.locator(".ant-table-tbody tr.ant-table-row:visible")
        for attempt in range(3):
            search.fill("")
            search.fill(keyword)
            assert self._click_visible_text_button(product, "查"), "商品弹窗未找到查询按钮"
            self.page.wait_for_timeout(1000)
            rows = product.locator(".ant-table-tbody tr.ant-table-row:visible")
            if rows.count():
                break
            log.warning(f"商品查询无结果，重试 {attempt + 1}/3：{keyword}")
            self.page.wait_for_timeout(800)
        assert rows.count() > 0, f"商品查询无结果：{keyword}（已重试 3 次）"
        if mode == "dblclick":
            rows.first.dblclick()
        else:
            rows.first.locator("input[type=checkbox]").check()
            assert self._click_visible_text_button(product, "确 定"), "商品弹窗未找到确定按钮"
        self.page.wait_for_timeout(500)

    def fill_editable_quantity(self, value: str | int, row_index: int = 0) -> None:
        log.info(f"activating 填写数量 {value} row={row_index}")
        row = self.get_editable_rows().nth(row_index)
        row.locator("input[id^='operNumber_']").fill(str(value))

    def fill_editable_price(self, value: str | int, row_index: int = 0) -> None:
        log.info(f"activating 填写单价 {value} row={row_index}")
        row = self.get_editable_rows().nth(row_index)
        row.locator("input[id^='unitPrice_']").fill(str(value))

    def fill_change_amount(self, value: str | int) -> None:
        log.info(f"activating 填写本次退款/付款 {value}")
        self.page.locator("#changeAmount:visible").fill(str(value))

    def get_debt_value(self) -> str:
        return self.page.locator("#debt:visible").input_value().strip()

    @allure.step("按商品信息查询：{keyword}")
    def search_by_product(self, keyword: str) -> None:
        log.info(f"activating 按商品信息查询 {keyword}")
        self.dismiss_guide()
        input_box = self._first_visible(
            self.page.get_by_placeholder(re.compile("请输入条码、名称、助记码"))
        )
        input_box.fill("")
        input_box.fill(keyword)
        self._click_button(re.compile(r"查\s*询"))
        self._wait_table_idle()

    @allure.step("保存单据")
    def click_save_only(self) -> None:
        log.info("activating 保存单据")
        assert self._click_visible_text_button(self.get_modal(), "保存（Ctrl+S）"), "未找到保存按钮"
        self.page.wait_for_timeout(1500)

    @allure.step("保存并审核单据")
    def click_save_and_audit(self) -> None:
        log.info("activating 保存并审核")
        assert self._click_visible_text_button(self.get_modal(), "保存并审核"), "未找到保存并审核按钮"
        self.page.wait_for_timeout(2000)

    def wait_modal_closed(self, timeout: int = 15000) -> None:
        deadline = time.time() + timeout / 1000
        while time.time() < deadline:
            if self.page.locator(".ant-modal:visible").count() == 0:
                return
            self.page.wait_for_timeout(300)
        raise AssertionError("单据弹窗未关闭")

    def select_supplier(self, name: str) -> None:
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

    def select_account(self, name: str) -> None:
        modal = self.get_modal()
        item = modal.locator(".ant-form-item").filter(has_text="结算账户").first
        item.locator(".ant-select").first.click()
        self.page.wait_for_timeout(400)
        self.page.locator(".ant-select-dropdown:visible li").filter(has_text=name).first.click()
        self.page.wait_for_timeout(300)

    def select_warehouse(self, name: str | None = None, row_index: int = 0) -> None:
        log.info(f"activating 选择仓库 {name or '第一个可用仓库'} row={row_index}")
        row = self.get_editable_rows().nth(row_index)
        row.locator(".ant-select").first.click()
        self.page.wait_for_timeout(400)
        options = self.page.locator(".ant-select-dropdown:visible li")
        assert options.count() > 0, "仓库下拉无选项"
        if name:
            options.filter(has_text=name).first.click()
        else:
            options.first.click()
        self.page.wait_for_timeout(300)

    def get_list_row(self, number: str) -> Locator:
        return self.get_rows().filter(has_text=number).first

    def select_list_row(self, number: str) -> None:
        row = self.get_list_row(number)
        row.locator("input[type=checkbox]").first.check()
        self.page.wait_for_timeout(300)

    def get_status(self, number: str) -> str:
        return self.get_list_row(number).locator(".ant-tag").first.inner_text().strip()

    @allure.step("审核单据：{number}")
    def audit(self, number: str) -> None:
        log.info(f"activating 审核 {number}")
        self.select_list_row(number)
        assert self._click_visible_text_button(self.page, "审", exclude="反"), "未找到审核按钮"
        self.page.wait_for_timeout(400)
        self.confirm_popup()
        self.page.wait_for_timeout(800)

    @allure.step("反审核单据：{number}")
    def unaudit(self, number: str) -> None:
        log.info(f"activating 反审核 {number}")
        self.select_list_row(number)
        assert self._click_visible_text_button(self.page, "反审核"), "未找到反审核按钮"
        self.page.wait_for_timeout(400)
        self.confirm_popup()
        self.page.wait_for_timeout(800)

    @allure.step("关联单据：{number}")
    def link_bill(self, input_id: str, number: str, bill_mode: str = "check") -> None:
        log.info(f"activating 关联单据 {input_id} -> {number} mode={bill_mode}")
        modal = self.get_modal()
        modal.locator(f"#{input_id}").locator(
            "xpath=..//i[contains(@class,'anticon-search')]"
        ).first.click()
        self.page.wait_for_timeout(800)

        link_modal = self.page.locator(".ant-modal:visible").last
        search = link_modal.locator("input[placeholder*='单据编号']:visible").first
        search.fill(number)
        assert self._click_visible_text_button(link_modal, "查"), "关联弹窗未找到查询按钮"
        self.page.wait_for_timeout(900)

        rows = link_modal.locator(".ant-table-tbody tr.ant-table-row:visible")
        assert rows.count() > 0, f"关联弹窗查询无数据：{number}"
        if bill_mode == "dblclick":
            rows.first.dblclick()
        else:
            checkbox = rows.first.locator("input[type=checkbox], input[type=radio]")
            if checkbox.count():
                checkbox.first.check()
            else:
                rows.first.click()
            assert self._click_visible_text_button(link_modal, "确 定"), "关联弹窗未找到确定按钮"
        self.page.wait_for_timeout(700)

        detail_modal = self.page.locator(".ant-modal:visible").last
        detail_rows = detail_modal.locator(".ant-table-tbody tr.ant-table-row:visible")
        assert detail_rows.count() > 0, "单据明细为空"
        detail_rows.first.locator("input[type=checkbox]").check()
        assert self._click_visible_text_button(detail_modal, "确 定"), "明细弹窗未找到确定按钮"
        self.page.wait_for_timeout(700)

    def has_link_data(self, input_id: str, number: str) -> bool:
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

    @allure.step("点击列表行操作：{number} / {action}")
    def click_row_action(self, number: str, action: str) -> None:
        log.info(f"activating 点击行操作 {number} / {action}")
        row = self.get_list_row(number)
        row.get_by_text(action, exact=True).first.click()
        self.page.wait_for_timeout(600)

    def get_messages(self) -> list[str]:
        locator = self.page.locator(
            ".ant-message-notice-content:visible, .ant-form-explain:visible, "
            ".ant-form-item-explain:visible"
        )
        return [
            locator.nth(i).inner_text().strip()
            for i in range(locator.count())
            if locator.nth(i).inner_text().strip()
        ]

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
