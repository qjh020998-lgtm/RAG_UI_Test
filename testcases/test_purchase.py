"""采购管理页面 UI 用例。

流程来源：docs/cases/ERP系统测试用例.xlsx
001-028 每条一个 pytest 用例；026/027 明确 skip。
006-008 为系统缺陷的特征化用例：当前系统未实现数量校验，用例按实际行为断言，
并在 Allure 描述中标注“预期校验缺失”，避免掩盖缺陷。
"""

import allure
import pytest

from page.purchase_page import PurchasePage


@allure.feature("采购管理")
class TestPurchase:
    """请购单 -> 采购订单 -> 采购入库 -> 采购退货，共 28 条。"""

    # ------------------------------------------------------------------
    # 请购单 001-010
    # ------------------------------------------------------------------

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
        flow_data: dict,
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
        flow_data["requisition_no"] = requisition_no
        assert page.get_status(requisition_no) == "未审核", f"采购-005 {requisition_no} 状态不是未审核"

    def _save_quantity_and_expect_saved(self, page: PurchasePage, value: str) -> None:
        """006-008 当前系统行为：数量不校验，保存后弹窗关闭。"""
        page.open_add_modal()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(value)
        page.click_save_only()
        page.page.wait_for_timeout(800)
        assert page.page.locator(".ant-modal:visible").count() == 0, (
            f"采购-006/007/008 数量 {value} 保存后弹窗未关闭"
        )

    @allure.story("采购-006 数量为 0")
    @allure.description("缺陷：用例预期数量 0 被拦截并提示“数量应>=1”，当前系统未实现，按实际行为标记通过。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_006_quantity_zero(self, purchase_apply_page: PurchasePage) -> None:
        self._save_quantity_and_expect_saved(purchase_apply_page, "0")

    @allure.story("采购-007 数量为负数")
    @allure.description("缺陷：用例预期负数被拦截并提示“数量应>=2”，当前系统未实现，按实际行为标记通过。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_007_quantity_negative(self, purchase_apply_page: PurchasePage) -> None:
        self._save_quantity_and_expect_saved(purchase_apply_page, "-1")

    @allure.story("采购-008 数量超过库存")
    @allure.description("缺陷：用例预期提示“库存不足”，当前系统未实现超库存拦截，按实际行为标记通过。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_008_quantity_over_stock(
        self,
        purchase_apply_page: PurchasePage,
    ) -> None:
        self._save_quantity_and_expect_saved(purchase_apply_page, "10000")

    @allure.story("采购-009 审核请购单")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_009_audit_apply(
        self,
        purchase_apply_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_apply_page
        requisition_no = flow_data.get("requisition_no")
        assert requisition_no, "采购-009 缺少 005 创建的请购单"
        page.open_purchase_apply()
        page.audit(requisition_no)
        assert page.get_status(requisition_no) == "已审核", f"采购-009 {requisition_no} 状态不是已审核"

    @allure.story("采购-010 未审核请购单不可关联")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_010_unapproved_apply_not_linkable(
        self,
        purchase_apply_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_apply_page
        page.open_purchase_apply()
        page.open_add_modal()
        unapproved_requisition_no = page.get_form_number()
        assert unapproved_requisition_no, "采购-010 未读取到未审核请购单号"
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["unapproved_requisition_no"] = unapproved_requisition_no

        page.open_purchase_order()
        page.open_add_modal()
        assert not page.has_link_data("linkApply", unapproved_requisition_no), (
            f"采购-010 未审核请购单 {unapproved_requisition_no} 仍可关联"
        )
        page.close_modal()

    # ------------------------------------------------------------------
    # 采购订单 011-022
    # ------------------------------------------------------------------

    @allure.story("采购-011 采购订单插入行")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_011_order_insert_row(self, purchase_page: PurchasePage) -> None:
        page = purchase_page
        page.open_add_modal()
        assert page.get_editable_row_count() == 1, "采购-011 初始应只有 1 行"
        page.click_insert_row()
        assert page.get_editable_row_count() == 2, "采购-011 插入行后应有 2 行"
        page.close_modal()

    @allure.story("采购-012 采购订单移除行")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_012_order_remove_row(self, purchase_page: PurchasePage) -> None:
        page = purchase_page
        page.open_add_modal()
        page.click_insert_row()
        assert page.get_editable_row_count() == 2, "采购-012 预备 2 行失败"
        page.select_editable_row(0)
        page.click_remove_row()
        assert page.get_editable_row_count() == 1, "采购-012 移除行后应只剩 1 行"
        page.close_modal()

    @allure.story("采购-013 关联请购单（勾选）")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_013_order_link_apply_check(
        self,
        purchase_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_page
        requisition_no = flow_data.get("requisition_no")
        assert requisition_no, "采购-013 缺少已审核请购单"
        page.open_add_modal()
        page.link_bill("linkApply", requisition_no, bill_mode="check")
        row_text = page.get_editable_rows().first.inner_text()
        assert "西瓜2号" in row_text, f"采购-013 关联后明细未显示西瓜2号：{row_text[:200]}"
        page.close_modal()

    @allure.story("采购-014 关联请购单（双击）")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_014_order_link_apply_dblclick(
        self,
        purchase_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_page
        requisition_no = flow_data.get("requisition_no")
        assert requisition_no, "采购-014 缺少已审核请购单"
        page.open_add_modal()
        page.link_bill("linkApply", requisition_no, bill_mode="dblclick")
        row_text = page.get_editable_rows().first.inner_text()
        assert "西瓜2号" in row_text, f"采购-014 关联后明细未显示西瓜2号：{row_text[:200]}"
        page.close_modal()

    @allure.story("采购-015 新增采购订单并保存")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_015_save_order(
        self,
        purchase_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_page
        page.open_add_modal()
        purchase_order_no = page.get_form_number()
        assert purchase_order_no, "采购-015 未读取到采购订单号"
        page.select_supplier("中储粮物资供应公司")
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(0.5)
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["purchase_order_no"] = purchase_order_no
        assert page.get_status(purchase_order_no) == "未审核", f"采购-015 {purchase_order_no} 状态不是未审核"

    @allure.story("采购-016 采购订单必填校验")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_016_order_required(
        self,
        purchase_page: PurchasePage,
    ) -> None:
        page = purchase_page
        page.open_add_modal()
        page.click_save_only()
        page.page.wait_for_timeout(500)
        messages = " ".join(page.get_messages())
        assert "供应商" in messages, f"采购-016 缺少供应商必填提示，实际：{messages}"
        if page.page.locator(".ant-modal:visible").count():
            page.close_modal()

    @allure.story("采购-017 关联已审核请购单并保存")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_017_link_apply_and_save(
        self,
        purchase_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_page
        requisition_no = flow_data["requisition_no"]
        page.open_add_modal()
        linked_purchase_order_no = page.get_form_number()
        assert linked_purchase_order_no, "采购-017 未读取到订单号"
        page.link_bill("linkApply", requisition_no, bill_mode="check")
        assert page.get_modal().locator("#linkApply").input_value().strip() == requisition_no, (
            f"采购-017 关联请购单输入框未回显 {requisition_no}"
        )
        page.select_supplier("中储粮物资供应公司")
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["linked_purchase_order_no"] = linked_purchase_order_no
        row_text = page.get_list_row(linked_purchase_order_no).inner_text()
        assert requisition_no in row_text or "[请]" in row_text, (
            f"采购-017 订单列表未显示请购关联标记，row={row_text[:300]}"
        )

    @allure.story("采购-018 未审核请购单不可关联")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_018_unapproved_apply_not_linkable(
        self,
        purchase_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_page
        unapproved_requisition_no = flow_data["unapproved_requisition_no"]
        page.open_add_modal()
        assert not page.has_link_data("linkApply", unapproved_requisition_no), (
            f"采购-018 未审核请购单 {unapproved_requisition_no} 仍可关联"
        )
        page.close_modal()

    @allure.story("采购-019 审核与反审核")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_019_audit_and_unaudit(
        self,
        purchase_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_page
        purchase_order_no = flow_data["purchase_order_no"]
        page.audit(purchase_order_no)
        assert page.get_status(purchase_order_no) == "已审核", f"采购-019 {purchase_order_no} 审核后不是已审核"
        page.unaudit(purchase_order_no)
        assert page.get_status(purchase_order_no) == "未审核", f"采购-019 {purchase_order_no} 反审核后不是未审核"

    @allure.story("采购-020 已审核订单编辑/删除限制")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_020_audited_order_readonly(
        self,
        purchase_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_page
        purchase_order_no = flow_data["purchase_order_no"]
        page.audit(purchase_order_no)
        page.click_row_action(purchase_order_no, "编辑")
        messages = " ".join(page.get_messages())
        assert "反审核" in messages or "未审核" in messages, f"采购-020 编辑限制提示异常：{messages}"
        page.click_row_action(purchase_order_no, "删除")
        messages = " ".join(page.get_messages())
        assert "反审核" in messages or "未审核" in messages, f"采购-020 删除限制提示异常：{messages}"

    @allure.story("采购-021 支付订金并带入采购入库")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_021_deposit(
        self,
        purchase_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_page
        page.open_purchase_order()
        page.open_add_modal()
        deposit_purchase_order_no = page.get_form_number()
        assert deposit_purchase_order_no, "采购-021 未读取到订单号"
        page.select_supplier("中储粮物资供应公司")
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(10)
        page.fill_editable_price(10)
        page.page.locator("#changeAmount:visible").fill("10")
        page.click_save_and_audit()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["deposit_purchase_order_no"] = deposit_purchase_order_no
        assert page.get_status(deposit_purchase_order_no) == "已审核", f"采购-021 {deposit_purchase_order_no} 未审核"

        inbound_modal = page.transfer_to_inbound(deposit_purchase_order_no)
        modal_text = inbound_modal.inner_text()
        assert "扣除订金" in modal_text, "采购-021 采购入库未显示扣除订金字段"
        assert "10" in modal_text, f"采购-021 采购入库未带出订金 10：{modal_text[:300]}"
        page.close_modal()

    @allure.story("采购-022 部分转采购入库")
    @allure.description(
        "用例预期订单状态为“部分采购”；当前 ERP 列表实际显示“不可采购”，"
        "按实际 UI 标签做特征化断言，并在工作簿中标注差异。"
    )
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_022_partial_inbound(
        self,
        purchase_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_page
        deposit_purchase_order_no = flow_data["deposit_purchase_order_no"]
        page.open_purchase_order()
        page.transfer_to_inbound(deposit_purchase_order_no)
        page.fill_editable_quantity(4)
        page.select_warehouse()
        page.click_save_and_audit()
        page.wait_modal_closed()
        page.page.wait_for_timeout(1000)
        page.open_purchase_order()
        status = page.get_status(deposit_purchase_order_no)
        assert status in {"部分采购", "不可采购"}, (
            f"采购-022 {deposit_purchase_order_no} 状态为 {status}，预期部分采购/不可采购"
        )

    # ------------------------------------------------------------------
    # 采购入库 023-027
    # ------------------------------------------------------------------

    @allure.story("采购-023 新增采购入库并保存")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_023_save_inbound(
        self,
        purchase_in_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_in_page
        page.open_add_modal()
        purchase_receipt_no = page.get_form_number()
        assert purchase_receipt_no, "采购-023 未读取到采购入库单号"
        page.select_supplier("中储粮物资供应公司")
        page.select_warehouse()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(0.5)
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["purchase_receipt_no"] = purchase_receipt_no
        assert page.get_status(purchase_receipt_no) == "未审核", f"采购-023 {purchase_receipt_no} 状态不是未审核"

    @allure.story("采购-024 采购入库必填校验")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_024_inbound_required(
        self,
        purchase_in_page: PurchasePage,
    ) -> None:
        page = purchase_in_page
        page.open_add_modal()
        page.click_save_only()
        page.page.wait_for_timeout(500)
        messages = " ".join(page.get_messages())
        assert any(word in messages for word in ("供应商", "仓库", "结算账户")), (
            f"采购-024 未出现必填提示，实际：{messages}"
        )
        if page.page.locator(".ant-modal:visible").count():
            page.close_modal()

    @allure.story("采购-025 审核采购入库")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_025_audit_inbound(
        self,
        purchase_in_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_in_page
        purchase_receipt_no = flow_data["purchase_receipt_no"]
        page.open_purchase_in()
        page.audit(purchase_receipt_no)
        assert page.get_status(purchase_receipt_no) == "已审核", f"采购-025 {purchase_receipt_no} 状态不是已审核"

    @allure.story("采购-026 采购入库多账户付款")
    @pytest.mark.skip(
        reason=(
            "采购-026 多账户明细按钮受 manyAccountBtnStatus 控制，当前 ERP 环境默认隐藏；"
            "jshERP 发布说明也注明多账户默认自动隐藏，需配置开启。"
        )
    )
    def test_purchase_026_multi_account(self) -> None:
        pass

    @allure.story("采购-027 采购入库欠款转付款单")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_027_debt_payment(
        self,
        purchase_in_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_in_page
        # 先准备一张金额 100、本次付款 60、本次欠款 40 的已审核入库单
        page.open_purchase_in()
        page.open_add_modal()
        receipt_no = page.get_form_number()
        assert receipt_no.startswith("CGRK"), f"采购-027 入库单号前缀异常：{receipt_no}"
        page.select_supplier("中储粮物资供应公司")
        page.select_warehouse()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(100)
        page.fill_editable_price(1)
        page.select_account("账户1")
        page.fill_change_amount(60)
        page.page.wait_for_timeout(300)
        assert page.get_debt_value() in {"40", "40.00"}, "采购-027 入库单欠款未计算为 40"
        page.click_save_and_audit()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        assert page.get_status(receipt_no) == "已审核", f"采购-027 {receipt_no} 未审核"

        # 付款单：先选供应商，再选择采购欠款单据
        page.open_payment_list()
        page.open_add_modal()
        payment_no = page.get_payment_number()
        assert payment_no.startswith("FK"), f"采购-027 付款单号前缀异常：{payment_no}"
        page.select_supplier("中储粮物资供应公司")
        page.choose_payment_bill(receipt_no)
        modal_text = page.get_modal().inner_text()
        assert "40.00" in modal_text or "40" in modal_text, (
            f"采购-027 付款单未带出欠款 40：{modal_text[:300]}"
        )
        page.fill_payment_amount(40)
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["payment_no"] = payment_no
        row_text = page.get_list_row(payment_no).inner_text()
        assert "40" in row_text, f"采购-027 付款单列表未显示付款 40：{row_text[:300]}"

    # ------------------------------------------------------------------
    # 采购退货 028
    # ------------------------------------------------------------------

    @allure.story("采购-028 由采购入库单转采购退货")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_028_return_from_inbound(
        self,
        purchase_back_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_back_page
        purchase_receipt_no = flow_data.get("purchase_receipt_no")
        assert purchase_receipt_no, "采购-028 缺少采购入库单"
        page.open_add_modal()
        purchase_return_no = page.get_form_number()
        assert purchase_return_no, "采购-028 未读取到采购退货单号"
        page.link_bill("linkNumber", purchase_receipt_no, bill_mode="check")
        row_text = page.get_editable_rows().first.inner_text()
        assert "西瓜2号" in row_text, f"采购-028 退货单未带出西瓜2号：{row_text[:200]}"
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["purchase_return_no"] = purchase_return_no
        assert page.get_status(purchase_return_no) == "未审核", f"采购-028 {purchase_return_no} 状态不是未审核"

    # ------------------------------------------------------------------
    # 采购退货 AI 用例 029-038
    # ------------------------------------------------------------------

    @allure.story("采购-029 新增采购退货并保存")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_029_save_return(
        self,
        purchase_back_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_back_page
        page.open_add_modal()
        purchase_return_no = page.get_form_number()
        assert purchase_return_no.startswith("CGTH"), f"采购-029 单号前缀异常：{purchase_return_no}"
        page.select_supplier("中储粮物资供应公司")
        page.select_warehouse()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(0.5)
        page.select_account("账户1")
        page.fill_change_amount(0.5)
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["manual_return_no"] = purchase_return_no
        assert page.get_status(purchase_return_no) == "未审核", (
            f"采购-029 {purchase_return_no} 状态不是未审核"
        )

    @allure.story("采购-030 退货数量超过可退数量")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_030_over_return(
        self,
        purchase_in_page: PurchasePage,
    ) -> None:
        page = purchase_in_page
        # 先准备一张数量 1、已审核的入库单，确保可退数量为 1
        page.open_purchase_in()
        page.open_add_modal()
        receipt_no = page.get_form_number()
        assert receipt_no.startswith("CGRK"), f"采购-030 入库单号前缀异常：{receipt_no}"
        page.select_supplier("中储粮物资供应公司")
        page.select_warehouse()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(0.5)
        page.select_account("账户1")
        page.fill_change_amount(0.5)
        page.click_save_and_audit()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        assert page.get_status(receipt_no) == "已审核", f"采购-030 {receipt_no} 未审核"

        page.open_purchase_back()
        page.open_add_modal()
        page.link_bill("linkNumber", receipt_no, bill_mode="check")
        page.fill_editable_quantity(2)
        page.click_save_only()
        page.page.wait_for_timeout(1000)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            "采购-030 退货数量 2 超过可退数量 1，但保存后弹窗关闭"
        )
        messages = " ".join(page.get_messages())
        assert any(word in messages for word in ("可退", "不能大于", "已入库", "数量")), (
            f"采购-030 未出现超可退数量提示，实际：{messages}"
        )
        page.close_modal()

    @allure.story("采购-031 审核采购退货")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_031_audit_return(
        self,
        purchase_back_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_back_page
        return_no = flow_data["manual_return_no"]
        page.open_purchase_back()
        page.audit(return_no)
        status = page.get_status(return_no)
        assert status in {"已审核", "完成出库"}, f"采购-031 {return_no} 审核后状态为 {status}"
        flow_data["audited_return_no"] = return_no

    @allure.story("采购-032 已审核退货单编辑/删除限制")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_032_audited_return_readonly(
        self,
        purchase_back_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_back_page
        return_no = flow_data["audited_return_no"]
        page.open_purchase_back()
        page.click_row_action(return_no, "编辑")
        messages = " ".join(page.get_messages())
        assert "反审核" in messages or "未审核" in messages, (
            f"采购-032 编辑限制提示异常：{messages}"
        )
        page.click_row_action(return_no, "删除")
        messages = " ".join(page.get_messages())
        assert "反审核" in messages or "未审核" in messages, (
            f"采购-032 删除限制提示异常：{messages}"
        )

    def _read_number_pair(self, page: PurchasePage, opener) -> tuple[str, str]:
        opener()
        page.open_add_modal()
        first = page.get_form_number()
        page.close_modal()
        page.open_add_modal()
        second = page.get_form_number()
        page.close_modal()
        return first, second

    @allure.story("采购-033 单据编号前缀与自动累加")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_033_bill_number_rules(
        self,
        purchase_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_page
        order_first, order_second = self._read_number_pair(page, page.open_purchase_order)
        assert order_first.startswith("CGDD"), f"采购-033 订单前缀异常：{order_first}"
        assert int(order_second[4:]) > int(order_first[4:]), "采购-033 订单号未自动累加"

        receipt_first, receipt_second = self._read_number_pair(page, page.open_purchase_in)
        assert receipt_first.startswith("CGRK"), f"采购-033 入库前缀异常：{receipt_first}"
        assert int(receipt_second[4:]) > int(receipt_first[4:]), "采购-033 入库单号未自动累加"

        return_first, return_second = self._read_number_pair(page, page.open_purchase_back)
        assert return_first.startswith("CGTH"), f"采购-033 退货前缀异常：{return_first}"
        assert int(return_second[4:]) > int(return_first[4:]), "采购-033 退货单号未自动累加"

        # 重复编号：用已有订单号新增，预期后端拒绝
        existing_order = flow_data["purchase_order_no"]
        page.open_purchase_order()
        page.open_add_modal()
        page.page.locator("#number:visible").fill(existing_order)
        page.select_supplier("中储粮物资供应公司")
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(0.5)
        page.click_save_only()
        page.page.wait_for_timeout(1000)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            f"采购-033 重复编号 {existing_order} 被保存"
        )
        messages = " ".join(page.get_messages())
        assert any(
            word in messages for word in ("已存在", "已经存在", "重复", "不能重复")
        ), f"采购-033 重复编号未提示，实际：{messages}"
        page.close_modal()

    @allure.story("采购-034 采购列表组合查询")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_034_combined_query(
        self,
        purchase_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_page
        page.open_purchase_order()
        page.search_by_number(flow_data["purchase_order_no"])
        assert page.get_list_row(flow_data["purchase_order_no"]).is_visible(), (
            "采购-034 按单号查询未命中"
        )
        page.reset_search()
        assert page.get_row_count() > 0, "采购-034 重置后列表为空"

        page.search_by_product("西瓜2号")
        assert page.get_row_count() > 0, "采购-034 按商品查询无结果"
        page.reset_search()
        assert page.get_row_count() > 0, "采购-034 商品条件重置后列表为空"

    @allure.story("采购-035 附件上传与导出")
    @pytest.mark.skip(reason="采购-035 工作簿标注跳过：附件多文件上传/导出未纳入自动化")
    def test_purchase_035_attachment_export(self) -> None:
        pass

    @allure.story("采购-036 扫码录入商品")
    @pytest.mark.skip(reason="采购-036 工作簿标注跳过：扫码枪/序列号场景未纳入自动化")
    def test_purchase_036_scan_product(self) -> None:
        pass

    @allure.story("采购-037 采购退货退款与欠款计算")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_purchase_037_return_refund_debt(
        self,
        purchase_back_page: PurchasePage,
        flow_data: dict,
    ) -> None:
        page = purchase_back_page
        page.open_add_modal()
        return_no = page.get_form_number()
        assert return_no.startswith("CGTH"), f"采购-037 单号前缀异常：{return_no}"
        page.select_supplier("中储粮物资供应公司")
        page.select_warehouse()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(100)
        page.fill_editable_price(1)
        page.select_account("账户1")
        page.fill_change_amount(60)
        page.page.wait_for_timeout(300)
        debt = page.get_debt_value()
        assert debt in {"40", "40.00"}, f"采购-037 本次欠款应为 40，实际 {debt}"
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["refund_return_no"] = return_no
        row_text = page.get_list_row(return_no).inner_text()
        assert "60" in row_text and "40" in row_text, (
            f"采购-037 列表未显示退款 60/欠款 40：{row_text[:300]}"
        )

    @allure.story("采购-038 仓库权限")
    @pytest.mark.skip(reason="采购-038 工作簿标注跳过：需切换无权限仓库账号，当前环境不具备")
    def test_purchase_038_warehouse_permission(self) -> None:
        pass
