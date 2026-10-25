import re
import time
from datetime import date
from urllib.parse import urlparse

import allure
from playwright.sync_api import Page, Locator, expect
from data_loader import load_config
from pw_core.driver.driver_manager import DriverManager
from utils.logger import get_logger

log = get_logger(__name__)


class BasePage:
    """
    所有页面对象的父类。
    封装 Playwright 交互，提供日志记录、Allure 步骤和智能等待。
    """

    # Ant Design 弹窗容器（采购/零售等单据页通用）
    MODAL = ".ant-modal:visible"
    # 后置清理：子类覆盖 CLEANUP_PATHS 即可复用 cleanup_module
    CLEANUP_PATHS: dict[str, str] = {}
    CLEANUP_MAX_LOOPS = 30
    FORM_ERROR = ".ant-form-explain, .ant-form-item-explain"
    MESSAGE = ".ant-message-notice-content"
    MODAL_TITLE = ".ant-modal-title"
    TABLE_ROWS = ".ant-table:visible .ant-table-tbody tr.ant-table-row"
    TABLE = ".ant-table:visible"
    GUIDE_ROOT = ".introjs-overlay, .introjs-tooltip"

    def __init__(self):
        self.driver_manager = DriverManager()
        self.page: Page = self.driver_manager.get_driver().page
        self.base_url = load_config().base_url.rstrip('/')

    # ------------------------------------------------------------------
    # 弹窗通用
    # ------------------------------------------------------------------

    @allure.step("获取弹窗")
    def get_modal(self) -> Locator:
        log.info("activating 获取弹窗")
        return self.page.locator(self.MODAL).last

    @allure.step("等待弹窗关闭")
    def wait_modal_closed(self, timeout: int = 15000) -> None:
        log.info("activating 等待弹窗关闭")
        deadline = time.time() + timeout / 1000
        while time.time() < deadline:
            if self.page.locator(self.MODAL).count() == 0:
                return
            self.page.wait_for_timeout(300)
        raise AssertionError("单据弹窗未关闭")

    # ------------------------------------------------------------------
    # 单据通用（表格 / 弹窗确认 / 列表行）
    # ------------------------------------------------------------------

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

    def _open(self, path: str) -> "BasePage":
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

    @staticmethod
    def _click_visible_text_button(
        scope: Locator,
        text: str,
        exclude: str | None = None,
    ) -> bool:
        """按按钮 inner_text 点击（兼容 Ant Design 插入空格）。"""
        text_norm = text.replace(" ", "")
        exclude_norm = exclude.replace(" ", "") if exclude else None
        for index in range(scope.locator("button:visible").count()):
            button = scope.locator("button:visible").nth(index)
            try:
                label = button.inner_text().strip()
            except Exception:
                continue
            label_norm = label.replace(" ", "")
            if text_norm in label_norm and (
                exclude_norm is None or exclude_norm not in label_norm
            ):
                button.click()
                return True
        return False

    @allure.step("获取明细行")
    def get_editable_rows(self) -> Locator:
        log.info("activating 获取明细行")
        return self.get_modal().locator(".tbody .tr[id*='tbody-tr']:visible")

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

    @allure.step("获取列表行")
    def get_list_row(self, number: str) -> Locator:
        log.info("activating 获取列表行")
        return self.get_rows().filter(has_text=number).first

    def select_list_row(self, number: str) -> None:
        row = self.get_list_row(number)
        row.locator("input[type=checkbox]").first.check()
        self.page.wait_for_timeout(300)

    @allure.step("读取单据状态")
    def get_status(self, number: str) -> str:
        log.info("activating 读取单据状态")
        return self.get_list_row(number).locator(".ant-tag").first.inner_text().strip()

    # ------------------------------------------------------------------
    # 单据通用（表格 / 弹窗确认 / 增删改查 / 审核）
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

    def get_rows(self) -> Locator:
        return self.page.locator(self.TABLE_ROWS)

    def _read_product_stock(self, product: Locator, row: Locator) -> None:
        """从商品选择弹窗的“库存”列读取当前商品库存。"""
        try:
            headers = product.locator(".ant-table-thead th")
            header_texts = [
                headers.nth(i).inner_text().strip()
                for i in range(headers.count())
            ]
            index = header_texts.index("库存")
            cell_text = row.locator("td").nth(index).inner_text().strip()
            match = re.search(r"[\d,]+(?:\.\d+)?", cell_text)
            self.last_product_stock = match.group(0).replace(",", "") if match else ""
            log.info(f"activating 读取商品库存 {self.last_product_stock}")
            self.last_product_stock = match.group(0).replace(",", "") if match else ""
        except Exception as exc:
            log.warning(f"读取商品库存失败：{exc}")
            self.last_product_stock = ""

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
        self._read_product_stock(product, rows.first)
        if mode == "dblclick":
            rows.first.dblclick()
        else:
            rows.first.locator("input[type=checkbox]").check()
            assert self._click_visible_text_button(product, "确 定"), "商品弹窗未找到确定按钮"
        self.page.wait_for_timeout(500)

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

    @allure.step("新增明细行")
    def click_insert_row(self) -> None:
        log.info("activating 插入行")
        assert self._click_visible_text_button(self.get_modal(), "插入行"), "未找到插入行按钮"
        self.page.wait_for_timeout(400)

    @allure.step("点击列表行操作：{number} / {action}")
    def click_row_action(self, number: str, action: str) -> None:
        log.info(f"activating 点击行操作 {number} / {action}")
        row = self.get_list_row(number)
        row.get_by_text(action, exact=True).first.click()
        self.page.wait_for_timeout(600)

    @allure.step("保存并审核单据")
    def click_save_and_audit(self) -> None:
        log.info("activating 保存并审核")
        assert self._click_visible_text_button(self.get_modal(), "保存并审核"), "未找到保存并审核按钮"
        self.page.wait_for_timeout(2000)

    @allure.step("保存单据")
    def click_save_only(self) -> None:
        log.info("activating 保存单据")
        assert self._click_visible_text_button(self.get_modal(), "保存（Ctrl+S）"), "未找到保存按钮"
        self.page.wait_for_timeout(1500)

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

    @allure.step("填写明细单价")
    def fill_editable_price(self, value: str | int, row_index: int = 0) -> None:
        log.info("activating 填写明细单价")
        log.info(f"activating 填写单价 {value} row={row_index}")
        row = self.get_editable_rows().nth(row_index)
        row.locator("input[id^='unitPrice_']").fill(str(value))

    @allure.step("填写明细数量")
    def fill_editable_quantity(self, value: str | int, row_index: int = 0) -> None:
        log.info("activating 填写明细数量")
        log.info(f"activating 填写数量 {value} row={row_index}")
        row = self.get_editable_rows().nth(row_index)
        row.locator("input[id^='operNumber_']").fill(str(value))

    @allure.step("读取明细行数")
    def get_editable_row_count(self) -> int:
        log.info("activating 读取明细行数")
        return self.get_editable_rows().count()

    @allure.step("读取单据编号")
    def get_form_number(self, timeout: int = 15000) -> str:
        log.info("activating 读取单据编号")
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

    @allure.step("读取页面提示消息")
    def get_messages(self) -> list[str]:
        log.info("activating 读取页面提示消息")
        locator = self.page.locator(
            ".ant-alert:visible, .ant-alert-message:visible, "
            ".ant-notification-notice:visible, "
            ".ant-message-notice-content:visible, .ant-form-explain:visible, "
            ".ant-form-item-explain:visible, .ant-form-item-explain-error:visible"
        )
        return [
            locator.nth(i).inner_text().strip()
            for i in range(locator.count())
            if locator.nth(i).inner_text().strip()
        ]

    @allure.step("读取列表行数")
    def get_row_count(self) -> int:
        log.info("activating 读取列表行数")
        return self.get_rows().count()

    @allure.step("关联单据：{number}")
    def link_bill(self, input_id: str, number: str, bill_mode: str = "check") -> None:
        log.info(f"activating 关联单据 {input_id} -> {number} mode={bill_mode}")
        modal = self.get_modal()
        modal.locator(f"#{input_id}").locator(
            "xpath=..//i[contains(@class,'anticon-search')]"
        ).first.click()
        self.page.wait_for_timeout(800)

        # 关联弹窗是「最后一个含单据编号输入框的 modal」，不能盲选 .last
        link_modal = self.page.locator(".ant-modal:visible").filter(
            has=self.page.locator("input[placeholder*='单据编号']")
        ).last
        search = link_modal.locator("input[placeholder*='单据编号']:visible").first
        search.fill(number)
        assert self._click_visible_text_button(link_modal, "查"), "关联弹窗未找到查询按钮"

        # 轮询行数，最多 5 秒
        rows = link_modal.locator(".ant-table-tbody tr.ant-table-row:visible")
        for _ in range(16):
            if rows.count() > 0:
                break
            self.page.wait_for_timeout(300)
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

    @allure.step("重置查询条件")
    def reset_search(self) -> None:
        self.dismiss_guide()
        self._click_button(re.compile(r"重\s*置"))
        self._wait_table_idle()

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

    # ------------------------------------------------------------------
    # 后置清理（反审核 -> 删除今日单据）
    # ------------------------------------------------------------------

    def get_visible_bill_numbers(self) -> set[str]:
        """当前可见列表行的单据号集合，用于判断批量操作是否推进。"""
        numbers: set[str] = set()
        rows = self.get_rows()
        for index in range(rows.count()):
            row_text = rows.nth(index).inner_text()
            numbers.update(
                re.findall(r"(?:CGDD|CGRK|CGTH|QGD|LSCK|LSTH)\d{5,}", row_text)
            )
        return numbers

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

    def open_cleanup_module(self, module_key: str) -> "BasePage":
        path = self.CLEANUP_PATHS.get(module_key)
        if not path:
            raise RuntimeError(f"未知清理模块：{module_key}")
        log.info(f"activating 清理模块 {module_key}")
        return self._open(path)

    def expand_search_filters(self) -> None:
        self.dismiss_guide()
        if self._status_form_item().count():
            return
        toggle = self.page.get_by_text(re.compile(r"展\s*开")).filter(visible=True)
        log.info(f"清理展开按钮数量={toggle.count()}")
        if toggle.count():
            toggle.first.click()
            self.page.wait_for_timeout(800)
        if not self._status_form_item().count():
            panel = self._search_panel()
            labels = [panel.locator("label").nth(i).inner_text() for i in range(panel.locator("label").count())]
            selects = [panel.locator(".ant-select").nth(i).inner_text() for i in range(panel.locator(".ant-select").count())]
            log.info(f"清理搜索面板 labels={labels}, selects={selects}, text={panel.inner_text()[:500]}")
            raise RuntimeError("列表展开后未找到“单据状态”筛选")

    def set_today_range(self) -> None:
        """将单据日期设置为今天~今天；失败则抛错，避免误删历史数据。"""
        today = date.today().strftime("%Y-%m-%d")
        inputs = self.page.locator("input.ant-calendar-range-picker-input:visible")
        if inputs.count() < 2:
            raise RuntimeError("列表未找到单据日期范围控件")
        start, end = inputs.nth(0), inputs.nth(1)

        # 日期范围输入框是 readonly，直接点击打开日历，再操作日历内的输入框
        try:
            start.click(force=True)
            self.page.wait_for_timeout(500)
            panel = self.page.locator(".ant-calendar-picker-container:visible").last
            if panel.count():
                panel.locator("input.ant-calendar-input").first.fill(today)
                panel.locator("input.ant-calendar-input").first.press("Enter")
                self.page.wait_for_timeout(400)

            end.click(force=True)
            self.page.wait_for_timeout(500)
            panel = self.page.locator(".ant-calendar-picker-container:visible").last
            if panel.count():
                panel.locator("input.ant-calendar-input").first.fill(today)
                panel.locator("input.ant-calendar-input").first.press("Enter")
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(400)
            if start.input_value().strip() == today and end.input_value().strip() == today:
                log.info(f"activating 清理日期范围 {today} ~ {today}")
                return
        except Exception as exc:
            log.warning(f"日期输入失败，尝试日历选择：{exc}")

        # 回退：通过日历中的今天单元格选择
        try:
            start.click(force=True)
            self.page.wait_for_timeout(300)
            panel = self.page.locator(".ant-calendar-picker-container:visible").last
            today_cell = panel.locator(f"td[title='{today}'], .ant-calendar-date[title='{today}']")
            if not today_cell.count():
                raise RuntimeError(f"日历中未找到今天：{today}")
            today_cell.first.click()
            end.click(force=True)
            self.page.wait_for_timeout(300)
            panel = self.page.locator(".ant-calendar-picker-container:visible").last
            today_cell = panel.locator(f"td[title='{today}'], .ant-calendar-date[title='{today}']")
            today_cell.last.click()
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(300)
            if start.input_value().strip() == today and end.input_value().strip() == today:
                log.info(f"activating 清理日期范围 {today} ~ {today}")
                return
        except Exception as exc:
            raise RuntimeError(f"设置今天日期范围失败：{exc}") from exc

        raise RuntimeError(
            f"设置今天日期范围失败：开始={start.input_value()!r}, 结束={end.input_value()!r}"
        )

    def select_bill_status(self, status: str) -> None:
        self.dismiss_guide()
        item = self._status_form_item()
        if not item.count():
            raise RuntimeError("未找到单据状态筛选项")
        select = item.locator(".ant-select").first
        select.click()
        self.page.wait_for_timeout(400)
        options = self.page.locator(".ant-select-dropdown:visible li")
        target = options.filter(has_text=status).first
        if not target.count():
            raise RuntimeError(f"单据状态下拉未找到：{status}")
        target.click()
        self.page.wait_for_timeout(300)
        log.info(f"activating 清理状态筛选 {status}")

    def click_search(self) -> None:
        self._click_button(re.compile(r"查\s*询"))
        self._wait_table_idle()
        self.page.wait_for_timeout(300)

    def select_all_current_page(self) -> int:
        rows = self.get_rows()
        count = rows.count()
        if count == 0:
            return 0
        header = self.page.locator(f"{self.TABLE} thead input[type=checkbox]").first
        if not header.count():
            raise RuntimeError("表格未找到全选复选框")
        if not header.is_checked():
            header.check(force=True)
            self.page.wait_for_timeout(300)
        selected = self.page.locator(
            f"{self.TABLE} tbody tr.ant-table-row input[type=checkbox]:checked"
        ).count()
        if selected < count:
            raise RuntimeError(f"全选未生效：行数={count}, 已选={selected}")
        log.info(f"activating 当前页全选 {count} 行")
        return count

    def batch_unaudit(self) -> None:
        if not self._click_visible_text_button(self.page, "反审核"):
            raise RuntimeError("未找到反审核按钮")
        self.page.wait_for_timeout(500)
        self.confirm_popup()
        self.page.wait_for_timeout(1000)
        log.info("activating 批量反审核")

    def batch_delete(self) -> None:
        if not self._click_visible_text_button(self.page, "删除"):
            raise RuntimeError("未找到删除按钮")
        self.page.wait_for_timeout(500)
        self.confirm_popup()
        self.page.wait_for_timeout(1000)
        log.info("activating 批量删除")

    def cleanup_module(self, module_key: str, dry_run: bool = False) -> dict:
        """清理今日已审核/未审核单据；其他状态跳过并记录。"""
        self.open_cleanup_module(module_key)
        self.expand_search_filters()
        self.set_today_range()
        self.select_bill_status("已审核")
        self.click_search()

        audited = 0
        for _ in range(self.CLEANUP_MAX_LOOPS):
            count = self.select_all_current_page()
            if count == 0:
                break
            if dry_run:
                audited += count
                log.info(f"[cleanup dry-run] {module_key} 已审核待反审核 {count} 条")
                break
            before_numbers = self.get_visible_bill_numbers()
            self.batch_unaudit()
            audited += count
            self.click_search()
            after_numbers = self.get_visible_bill_numbers()
            if before_numbers and before_numbers == after_numbers:
                raise RuntimeError(
                    f"{module_key} 反审核后当前页单据未变化，停止清理"
                )

        self.select_bill_status("未审核")
        self.click_search()
        deleted = 0
        for _ in range(self.CLEANUP_MAX_LOOPS):
            count = self.select_all_current_page()
            if count == 0:
                break
            if dry_run:
                deleted += count
                log.info(f"[cleanup dry-run] {module_key} 未审核待删除 {count} 条")
                break
            before_numbers = self.get_visible_bill_numbers()
            self.batch_delete()
            deleted += count
            self.click_search()
            after_numbers = self.get_visible_bill_numbers()
            if before_numbers and before_numbers == after_numbers:
                raise RuntimeError(
                    f"{module_key} 删除后当前页单据未变化，停止清理"
                )

        remaining = self.get_status_tag_texts()
        skipped = sorted(set(remaining) - {"未审核", "已审核"})
        result = {
            "module": module_key,
            "audited": audited,
            "deleted": deleted,
            "remaining_statuses": skipped,
        }
        log.info(f"[cleanup] {result}")
        return result

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
    # 首页初始化 / 用例后置
    # ------------------------------------------------------------------

    def close_intro_guide(self) -> None:
        """关闭 intro.js 新手引导遮罩；没有引导时立即返回。"""
        guide = self.page.locator(".introjs-overlay, .introjs-tooltip")
        if guide.count() == 0:
            return

        for selector in (
            ".introjs-skipbutton",
            ".introjs-donebutton",
            ".introjs-tooltip [class*='close']",
        ):
            buttons = self.page.locator(selector)
            for index in range(buttons.count()):
                item = buttons.nth(index)
                try:
                    if item.is_visible():
                        item.click(force=True)
                        self.page.wait_for_timeout(200)
                        break
                except Exception:
                    continue
            if guide.count() == 0:
                return

        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(200)
        if guide.count() == 0:
            return

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
        self.page.wait_for_timeout(100)

    def close_visible_modals(self) -> None:
        """关闭当前可见的 Ant Design 弹窗，为回首页清场。"""
        for _ in range(3):
            wraps = self.page.locator(".ant-modal-wrap:visible")
            if wraps.count() == 0:
                return

            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)
            if self.page.locator(".ant-modal-wrap:visible").count() == 0:
                return

            close_buttons = self.page.locator(".ant-modal-close:visible")
            if close_buttons.count():
                try:
                    close_buttons.first.click(force=True)
                except Exception:
                    pass
                self.page.wait_for_timeout(200)
                continue

            for label in (r"取\s*消", r"关\s*闭"):
                buttons = self.page.get_by_role("button", name=re.compile(label))
                clicked = False
                for index in range(buttons.count()):
                    item = buttons.nth(index)
                    try:
                        if item.is_visible():
                            item.click(force=True)
                            clicked = True
                            break
                    except Exception:
                        continue
                if clicked:
                    break
            self.page.wait_for_timeout(200)

    @allure.step("回到 ERP 首页")
    def back_to_home(self, home_url: str | None = None) -> None:
        """关闭引导/弹窗并回到 ERP 首页，供用例后置和测试类初始化使用。"""
        self.close_intro_guide()
        self.close_visible_modals()

        if home_url is None:
            parsed = urlparse(self.page.url)
            home_url = f"{parsed.scheme}://{parsed.netloc}/dashboard/analysis"

        clicked = False
        home_links = self.page.locator('a[href="/dashboard/analysis"]:visible')
        if home_links.count():
            try:
                home_links.first.click()
                clicked = True
            except Exception:
                clicked = False

        if not clicked:
            self.page.goto(home_url, wait_until="domcontentloaded")

        try:
            self.page.wait_for_url(
                re.compile(r".*/dashboard/analysis.*"),
                timeout=60000,
            )
        except Exception:
            self.page.goto(home_url, wait_until="domcontentloaded")
            self.page.wait_for_url(
                re.compile(r".*/dashboard/analysis.*"),
                timeout=60000,
            )

        self.page.locator(".ant-menu:visible").first.wait_for(
            state="visible",
            timeout=60000,
        )
        self.page.evaluate("window.scrollTo(0, 0)")
        log.info("已回到 ERP 首页")

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
