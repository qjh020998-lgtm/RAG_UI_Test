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
        self.page.goto(f"{self.base_url}{path}", wait_until="domcontentloaded")
        self.wait_for_load()
        self.wait_for_visible(self.TABLE, timeout=30000)
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
