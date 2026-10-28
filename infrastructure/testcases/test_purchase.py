"""采购管理页面 UI 用例（采购 001-020，一一对应）。"""

import allure
import pytest

from data_loader import load_yaml
from infrastructure.page.purchase_page import PurchasePage

PURCHASE_DATA = load_yaml("purchase.yaml")


@allure.feature("采购管理")
class TestPurchase:
    """采购管理 001-020。"""


    @allure.story("采购-001 插入行")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_001_insert_row(self, purchase_apply_page: PurchasePage) -> None:
        page = purchase_apply_page
        page.open_add_modal()
        assert page.get_editable_row_count() == 1, "采购-001 初始应只有 1 行"
        page.click_insert_row()
        assert page.get_editable_row_count() == 2, "采购-001 插入行后应有 2 行"
        page.close_modal()

    @allure.story("采购-002 移除行")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_002_remove_row(self, purchase_apply_page: PurchasePage) -> None:
        page = purchase_apply_page
        page.open_add_modal()
        page.click_insert_row()
        assert page.get_editable_row_count() == 2, "采购-002 预备 2 行失败"
        page.select_editable_row(0)
        page.click_remove_row()
        assert page.get_editable_row_count() == 1, "采购-002 移除行后应只剩 1 行"
        page.close_modal()

    @allure.story("采购-003 双击添加商品")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_003_add_product_dblclick(
        self,
        purchase_apply_page: PurchasePage,
    ) -> None:
        page = purchase_apply_page
        page.open_add_modal()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        row_text = page.get_editable_rows().first.inner_text()
        assert "西瓜2号" in row_text or "1033" in row_text, (
            f"采购-003 明细未显示西瓜2号/条码1033：{row_text[:200]}"
        )
        page.close_modal()

    @allure.story("采购-004 选中商品后确定")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_004_add_product_confirm(
        self,
        purchase_apply_page: PurchasePage,
    ) -> None:
        page = purchase_apply_page
        page.open_add_modal()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="confirm")
        row_text = page.get_editable_rows().first.inner_text()
        assert "西瓜2号" in row_text or "1033" in row_text, (
            f"采购-004 明细未显示西瓜2号/条码1033：{row_text[:200]}"
        )
        page.close_modal()

    @allure.story("采购-005 数量在库存内保存")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_005_save_valid_quantity(
        self,
        purchase_apply_page: PurchasePage,
    ) -> None:
        page = purchase_apply_page
        page.open_add_modal()
        requisition_no = page.get_form_number()
        assert requisition_no, "采购-005 未读取到请购单号"
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        assert page.get_status(requisition_no) == "未审核", f"采购-005 {requisition_no} 状态不是未审核"

    @allure.story("采购-006 数量为 0")
    @allure.description("缺陷：数量 0 应被拦截并提示“数量应>=1”；当前系统未校验，按业务预期断言应失败。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_006_quantity_zero(self, purchase_apply_page: PurchasePage) -> None:
        page = purchase_apply_page
        page.open_add_modal()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity("0")
        page.click_save_only()
        page.page.wait_for_timeout(800)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            "采购-006 Bug：数量 0 保存成功，弹窗关闭"
        )
        messages = " ".join(page.get_messages())
        assert "数量" in messages and any(token in messages for token in (">=1", "≥1")), (
            f"采购-006 数量 0 未提示“数量应>=1”，实际：{messages}"
        )

    @allure.story("采购-007 数量为负数")
    @allure.description("缺陷：负数数量应被拦截并提示数量下限；当前系统未校验，按业务预期断言应失败。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_007_quantity_negative(self, purchase_apply_page: PurchasePage) -> None:
        page = purchase_apply_page
        page.open_add_modal()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity("-1")
        page.click_save_only()
        page.page.wait_for_timeout(800)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            "采购-007 Bug：数量 -1 保存成功，弹窗关闭"
        )
        messages = " ".join(page.get_messages())
        assert "数量" in messages and any(
            token in messages for token in (">=1", "≥1", ">=2", "≥2")
        ), f"采购-007 负数数量未提示数量下限，实际：{messages}"

    @allure.story("采购-008 数量达到当前库存上限")
    @allure.description(
        "Bug 预期：商品选择弹窗显示库存，请购数量达到当前库存上限时应提示“库存已达上限”；"
        "当前系统未拦截则判定为缺陷。"
    )
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_008_quantity_at_stock_limit(
        self,
        purchase_apply_page: PurchasePage,
    ) -> None:
        page = purchase_apply_page
        page.open_add_modal()
        page.open_product_selector(0)
        page.choose_product("西瓜1号", mode="dblclick")
        stock_text = page.get_last_product_stock()
        assert stock_text.isdigit(), f"采购-008 未读取到商品库存：{stock_text!r}"
        stock = int(stock_text)
        assert stock > 0, f"采购-008 商品库存异常：{stock}"
        page.fill_editable_quantity(stock)
        page.click_save_only()
        page.page.wait_for_timeout(800)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            f"采购-008 Bug：数量等于库存上限 {stock} 仍保存成功，弹窗关闭"
        )
        messages = " ".join(page.get_messages())
        assert any(word in messages for word in ("库存", "上限", "已达", "超过")), (
            f"采购-008 数量达到库存上限未提示，实际：{messages}"
        )

    @allure.story("采购-009 数量超过当前库存上限")
    @allure.description(
        "Bug 预期：商品选择弹窗显示库存，请购数量超过当前库存上限 1 时应提示库存已达上限；"
        "当前系统未拦截则判定为缺陷。"
    )
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_009_quantity_over_stock_limit(
        self,
        purchase_apply_page: PurchasePage,
    ) -> None:
        page = purchase_apply_page
        page.open_add_modal()
        page.open_product_selector(0)
        page.choose_product("西瓜1号", mode="dblclick")
        stock_text = page.get_last_product_stock()
        assert stock_text.isdigit(), f"采购-009 未读取到商品库存：{stock_text!r}"
        stock = int(stock_text)
        assert stock > 0, f"采购-009 商品库存异常：{stock}"
        page.fill_editable_quantity(stock + 1)
        page.click_save_only()
        page.page.wait_for_timeout(800)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            f"采购-009 Bug：数量超过库存上限 {stock} 仍保存成功，弹窗关闭"
        )
        messages = " ".join(page.get_messages())
        assert any(word in messages for word in ("库存", "上限", "已达", "超过")), (
            f"采购-009 超过库存上限未提示，实际：{messages}"
        )

    @allure.story("采购-010 审核请购单")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_010_audit_apply(
        self,
        purchase_apply_page: PurchasePage,
    ) -> None:
        page = purchase_apply_page
        requisition_no = page.create_requisition(PURCHASE_DATA["product"], 1)
        assert requisition_no, "采购-010 缺少 005 创建的请购单"
        page.open_purchase_apply()
        page.audit(requisition_no)
        assert page.get_status(requisition_no) == "已审核", f"采购-010 {requisition_no} 状态不是已审核"

    @allure.story("采购-011 未审核请购单不可关联")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_011_unapproved_apply_not_linkable(
        self,
        purchase_apply_page: PurchasePage,
    ) -> None:
        page = purchase_apply_page
        page.open_purchase_apply()
        page.open_add_modal()
        unapproved_requisition_no = page.get_form_number()
        assert unapproved_requisition_no, "采购-011 未读取到未审核请购单号"
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)

        page.open_purchase_order()
        page.open_add_modal()
        assert not page.has_link_data("linkApply", unapproved_requisition_no), (
            f"采购-011 未审核请购单 {unapproved_requisition_no} 仍可关联"
        )
        page.close_modal()

    @allure.story("采购-012 采购订单插入行")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_012_order_insert_row(self, purchase_page: PurchasePage) -> None:
        page = purchase_page
        page.open_add_modal()
        assert page.get_editable_row_count() == 1, "采购-012 初始应只有 1 行"
        page.click_insert_row()
        assert page.get_editable_row_count() == 2, "采购-012 插入行后应有 2 行"
        page.close_modal()

    @allure.story("采购-013 采购订单移除行")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_013_order_remove_row(self, purchase_page: PurchasePage) -> None:
        page = purchase_page
        page.open_add_modal()
        page.click_insert_row()
        assert page.get_editable_row_count() == 2, "采购-013 预备 2 行失败"
        page.select_editable_row(0)
        page.click_remove_row()
        assert page.get_editable_row_count() == 1, "采购-013 移除行后应只剩 1 行"
        page.close_modal()

    @allure.story("采购-014 关联请购单（勾选）")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_014_order_link_apply_check(
        self,
        purchase_page: PurchasePage,
    ) -> None:
        page = purchase_page
        requisition_no = page.create_requisition(PURCHASE_DATA["product"], 1, audit=True)
        assert requisition_no, "采购-014 缺少已审核请购单"
        page.open_purchase_order()
        page.open_add_modal()
        page.link_bill("linkApply", requisition_no, bill_mode="check")
        row_text = page.get_editable_rows().first.inner_text()
        assert "西瓜2号" in row_text, f"采购-014 关联后明细未显示西瓜2号：{row_text[:200]}"
        page.close_modal()

    @allure.story("采购-015 关联请购单（双击）")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_015_order_link_apply_dblclick(
        self,
        purchase_page: PurchasePage,
    ) -> None:
        page = purchase_page
        requisition_no = page.create_requisition(PURCHASE_DATA["product"], 1, audit=True)
        assert requisition_no, "采购-015 缺少已审核请购单"
        page.open_purchase_order()
        page.open_add_modal()
        page.link_bill("linkApply", requisition_no, bill_mode="dblclick")
        row_text = page.get_editable_rows().first.inner_text()
        assert "西瓜2号" in row_text, f"采购-015 关联后明细未显示西瓜2号：{row_text[:200]}"
        page.close_modal()

    @allure.story("采购-016 新增采购订单并保存")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_016_save_order(
        self,
        purchase_page: PurchasePage,
    ) -> None:
        page = purchase_page
        page.open_add_modal()
        purchase_order_no = page.get_form_number()
        assert purchase_order_no, "采购-016 未读取到采购订单号"
        page.select_supplier("中储粮物资供应公司")
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(0.5)
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        assert page.get_status(purchase_order_no) == "未审核", f"采购-016 {purchase_order_no} 状态不是未审核"

    @allure.story("采购-017 采购订单必填校验")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_017_order_required(
        self,
        purchase_page: PurchasePage,
    ) -> None:
        page = purchase_page
        page.open_add_modal()
        page.click_save_only()
        page.page.wait_for_timeout(500)
        messages = " ".join(page.get_messages())
        assert "供应商" in messages, f"采购-017 缺少供应商必填提示，实际：{messages}"
        if page.page.locator(".ant-modal:visible").count():
            page.close_modal()

    @allure.story("采购-018 关联已审核请购单并保存")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_018_link_apply_and_save(
        self,
        purchase_page: PurchasePage,
    ) -> None:
        page = purchase_page
        requisition_no = page.create_requisition(PURCHASE_DATA["product"], 1, audit=True)
        page.open_purchase_order()
        page.open_add_modal()
        linked_purchase_order_no = page.get_form_number()
        assert linked_purchase_order_no, "采购-018 未读取到订单号"
        page.link_bill("linkApply", requisition_no, bill_mode="check")
        assert page.get_modal().locator("#linkApply").input_value().strip() == requisition_no, (
            f"采购-018 关联请购单输入框未回显 {requisition_no}"
        )
        page.select_supplier("中储粮物资供应公司")
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        row_text = page.get_list_row(linked_purchase_order_no).inner_text()
        assert requisition_no in row_text or "[请]" in row_text, (
            f"采购-018 订单列表未显示请购关联标记，row={row_text[:300]}"
        )

    @allure.story("采购-019 未审核请购单不可关联")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_019_unapproved_apply_not_linkable(
        self,
        purchase_page: PurchasePage,
    ) -> None:
        page = purchase_page
        unapproved_requisition_no = page.create_requisition(PURCHASE_DATA["product"], 1)
        page.open_purchase_order()
        page.open_add_modal()
        assert not page.has_link_data("linkApply", unapproved_requisition_no), (
            f"采购-019 未审核请购单 {unapproved_requisition_no} 仍可关联"
        )
        page.close_modal()

    @allure.story("采购-020 审核与反审核")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_020_audit_and_unaudit(
        self,
        purchase_page: PurchasePage,
    ) -> None:
        page = purchase_page
        purchase_order_no = page.create_order(PURCHASE_DATA["supplier"], PURCHASE_DATA["product"], 1, PURCHASE_DATA["price"], PURCHASE_DATA["account"])
        page.audit(purchase_order_no)
        assert page.get_status(purchase_order_no) == "已审核", f"采购-020 {purchase_order_no} 审核后不是已审核"
        page.unaudit(purchase_order_no)
        assert page.get_status(purchase_order_no) == "未审核", f"采购-020 {purchase_order_no} 反审核后不是未审核"
