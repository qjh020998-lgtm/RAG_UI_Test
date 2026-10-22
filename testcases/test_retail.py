"""零售管理页面 UI 用例。

来源：docs/cases/ERP系统测试用例 (1).xlsx / 零售页
零售-001 ~ 零售-032，每条一个 pytest 用例。
017-022、031-032 为边界/缺陷用例：按业务预期断言，系统未拦截时应失败并暴露 Bug。
"""

import allure
import pytest

from page.retail_page import RetailPage


@allure.feature("零售管理")
class TestRetail:
    """零售出库 + 零售退货，共 32 条。"""

    # ------------------------------------------------------------------
    # 零售出库 001-016
    # ------------------------------------------------------------------

    @allure.story("零售-001 零售出库列表加载")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_001_list_loaded(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        assert "/bill/retail_out" in page.page.url, f"零售出库 URL 异常：{page.page.url}"
        headers = page.get_list_headers()
        for header in ["会员", "单据编号", "商品信息", "单据日期", "操作员",
                       "数量", "金额合计", "收款金额", "找零", "状态"]:
            assert header in headers, f"零售-001 缺少表头：{header}，实际：{headers}"
        for button in ["新增", "删除", "转零售退货", "审核", "反审核", "列设置"]:
            assert page.has_button(button), f"零售-001 缺少按钮：{button}"

    @allure.story("零售-002 插入行")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_002_insert_row(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        page.open_add_modal()
        assert page.get_editable_row_count() == 1, "零售-002 初始应只有 1 行"
        page.click_insert_row()
        assert page.get_editable_row_count() == 2, "零售-002 插入行后应有 2 行"
        page.close_modal()

    @allure.story("零售-003 删除明细行")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_003_delete_row(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        page.open_add_modal()
        page.click_insert_row()
        assert page.get_editable_row_count() == 2, "零售-003 预备 2 行失败"
        page.select_editable_row(0)
        page.click_remove_row()
        assert page.get_editable_row_count() == 1, "零售-003 删除后应只剩 1 行"
        page.close_modal()

    @allure.story("零售-004 双击添加商品")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_004_add_product_dblclick(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        page.open_add_modal()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        row_text = page.get_editable_rows().first.inner_text()
        assert "西瓜2号" in row_text or "1033" in row_text, (
            f"零售-004 明细未显示西瓜2号/1033：{row_text[:200]}"
        )
        assert "斤" in row_text or "kg" in row_text.lower(), f"零售-004 未显示单位：{row_text[:200]}"
        page.close_modal()

    @allure.story("零售-005 勾选添加商品")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_005_add_product_confirm(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        page.open_add_modal()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="confirm")
        row_text = page.get_editable_rows().first.inner_text()
        assert "西瓜2号" in row_text or "1033" in row_text, (
            f"零售-005 明细未显示西瓜2号/1033：{row_text[:200]}"
        )
        page.close_modal()

    @allure.story("零售-006 非会员零售出库保存")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_006_save_cash_sale(
        self,
        retail_out_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_out_page
        page.open_add_modal()
        sale_no = page.get_form_number()
        assert sale_no.startswith("LSCK"), f"零售-006 单号前缀异常：{sale_no}"
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(1)
        page.fill_received_amount(1)
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["retail_sale_no"] = sale_no
        assert page.get_status(sale_no) == "未审核", f"零售-006 {sale_no} 状态不是未审核"
        assert "1" in page.get_list_row(sale_no).inner_text()

    @allure.story("零售-007 收款金额与找零")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_007_received_and_change(
        self,
        retail_out_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_out_page
        page.open_add_modal()
        sale_no = page.get_form_number()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(10)
        page.fill_received_amount(20)
        assert page.get_received_amount() == "20"
        assert page.get_change_amount() in {"10", "10.00"}, (
            f"零售-007 找零应为 10，实际 {page.get_change_amount()}"
        )
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["retail_change_sale_no"] = sale_no
        row_text = page.get_list_row(sale_no).inner_text()
        assert "20" in row_text and "10" in row_text, f"零售-007 列表值异常：{row_text[:300]}"

    @allure.story("零售-008 无商品明细保存被拦截")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_008_empty_sale_rejected(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        before = page.get_row_count()
        page.open_add_modal()
        page.click_save_only()
        page.page.wait_for_timeout(600)
        assert page.page.locator(".ant-modal:visible").count() > 0, "零售-008 无商品仍关闭弹窗"
        assert page.get_row_count() == before, "零售-008 无商品仍新增列表记录"
        page.close_modal()

    @allure.story("零售-009 选择会员卡号")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_009_select_member(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        page.open_add_modal()
        selected = page.select_member(0)
        assert selected, "零售-009 会员卡号下拉未返回选项"
        assert selected in page.get_member_text(), (
            f"零售-009 会员卡号未回显：{selected} / {page.get_member_text()}"
        )
        page.close_modal()

    @allure.story("零售-010 收款类型默认现付")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_010_payment_type(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        page.open_add_modal()
        assert page.get_payment_type_text() == "现付", (
            f"零售-010 收款类型默认值异常：{page.get_payment_type_text()}"
        )
        options = page.get_payment_type_options()
        assert "现付" in options, f"零售-010 收款类型无现付选项：{options}"
        page.close_modal()

    @allure.story("零售-011 保存并审核零售出库")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_011_save_and_audit(
        self,
        retail_out_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_out_page
        page.open_add_modal()
        sale_no = page.get_form_number()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(1)
        page.fill_received_amount(1)
        page.click_save_and_audit()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["audited_retail_sale_no"] = sale_no
        assert page.get_status(sale_no) == "已审核", f"零售-011 {sale_no} 状态不是已审核"

    @allure.story("零售-012 审核与反审核")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_012_audit_and_unaudit(
        self,
        retail_out_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_out_page
        sale_no = flow_data["retail_sale_no"]
        page.open_retail_out()
        page.audit(sale_no)
        assert page.get_status(sale_no) == "已审核", f"零售-012 {sale_no} 审核后不是已审核"
        page.unaudit(sale_no)
        assert page.get_status(sale_no) == "未审核", f"零售-012 {sale_no} 反审核后不是未审核"

    @allure.story("零售-013 已审核出库单不可编辑/删除")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_013_audited_sale_readonly(
        self,
        retail_out_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_out_page
        sale_no = flow_data["audited_retail_sale_no"]
        page.open_retail_out()
        page.click_row_action(sale_no, "编辑")
        messages = " ".join(page.get_messages())
        assert "反审核" in messages or "未审核" in messages, f"零售-013 编辑提示异常：{messages}"
        page.click_row_action(sale_no, "删除")
        messages = " ".join(page.get_messages())
        assert "反审核" in messages or "未审核" in messages, f"零售-013 删除提示异常：{messages}"

    @allure.story("零售-014 零售出库转零售退货")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_014_convert_to_return(
        self,
        retail_out_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_out_page
        sale_no = flow_data["audited_retail_sale_no"]
        page.open_retail_out()
        page.convert_to_retail_return(sale_no)
        assert page.get_linked_number() == sale_no, (
            f"零售-014 关联单据未回显：{page.get_linked_number()}"
        )
        row_text = page.get_editable_rows().first.inner_text()
        assert "西瓜2号" in row_text or "1033" in row_text, (
            f"零售-014 退货明细未带出商品：{row_text[:200]}"
        )
        page.close_modal()

    @allure.story("零售-015 按单号查询与重置")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_015_search_by_number(
        self,
        retail_out_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_out_page
        sale_no = flow_data["retail_sale_no"]
        page.open_retail_out()
        page.search_by_number(sale_no)
        assert page.get_list_row(sale_no).is_visible(), f"零售-015 未查询到 {sale_no}"
        page.reset_search()
        assert page.get_row_count() > 0, "零售-015 重置后列表为空"

    @allure.story("零售-016 按商品信息查询")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_016_search_by_product(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        page.open_retail_out()
        page.search_by_product("西瓜2号")
        assert page.get_row_count() > 0, "零售-016 按商品查询无结果"
        page.reset_search()
        assert page.get_row_count() > 0, "零售-016 商品条件重置后列表为空"

    @allure.story("零售-017 一次性删除全部明细行")
    @allure.description("边界：删除全部行后，不应再保留任何已添加商品；允许表格保留 1 行空白行。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_017_delete_all_rows(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        page.open_add_modal()
        page.click_insert_row()
        page.click_insert_row()
        assert page.get_editable_row_count() == 3, "零售-017 预备 3 行失败"
        rows = page.get_editable_rows()
        for index in range(rows.count()):
            rows.nth(index).locator("input[type=checkbox]").check()
        page.click_remove_row()
        count = page.get_editable_row_count()
        assert count <= 1, f"零售-017 删除全部行后仍保留 {count} 行"
        if count == 1:
            text = page.get_editable_rows().first.inner_text()
            assert "西瓜2号" not in text and "1033" not in text, (
                f"零售-017 删除后仍残留已添加商品：{text[:200]}"
            )
        page.close_modal()

    @allure.story("零售-018 连续插入大量明细行")
    @allure.description("边界：连续插入 50 行，页面应保持可操作且行数正确。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_018_many_rows(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        page.open_add_modal()
        for _ in range(50):
            page.click_insert_row()
        assert page.get_editable_row_count() == 51, (
            f"零售-018 连续插入 50 行后行数异常：{page.get_editable_row_count()}"
        )
        page.close_modal()

    @allure.story("零售-019 数量 0 保存")
    @allure.description("Bug 预期：数量 0 应被拦截并提示数量应>=1；实际若保存成功则为缺陷。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_019_quantity_zero(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        self._open_sale_with_values(page, "0", "1")
        page.click_save_only()
        page.page.wait_for_timeout(800)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            "零售-019 Bug：数量 0 保存成功，弹窗关闭"
        )
        messages = " ".join(page.get_messages())
        assert any(word in messages for word in ("数量", ">=1", "大于0")), (
            f"零售-019 数量 0 未出现预期提示：{messages}"
        )

    @allure.story("零售-020 数量为负数")
    @allure.description("Bug 预期：负数数量应被拦截；实际若保存成功则为缺陷。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_020_quantity_negative(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        self._open_sale_with_values(page, "-1", "1")
        page.click_save_only()
        page.page.wait_for_timeout(800)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            "零售-020 Bug：数量 -1 保存成功，弹窗关闭"
        )
        messages = " ".join(page.get_messages())
        assert any(word in messages for word in ("数量", "大于0", ">=1")), (
            f"零售-020 负数数量未出现预期提示：{messages}"
        )

    @allure.story("零售-021 数量超过库存")
    @allure.description("Bug 预期：数量超过库存应提示库存不足；实际若保存成功则为缺陷。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_021_quantity_over_stock(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        self._open_sale_with_values(page, "999999", "1")
        page.click_save_only()
        page.page.wait_for_timeout(800)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            "零售-021 Bug：数量 999999 超过库存仍保存成功"
        )
        messages = " ".join(page.get_messages())
        assert any(word in messages for word in ("库存", "不足", "数量")), (
            f"零售-021 超库存未出现预期提示：{messages}"
        )

    @allure.story("零售-022 单价为负数")
    @allure.description("Bug 预期：负数单价应被拦截；实际若保存成功则为缺陷。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_022_price_negative(self, retail_out_page: RetailPage) -> None:
        page = retail_out_page
        self._open_sale_with_values(page, "1", "-1")
        page.click_save_only()
        page.page.wait_for_timeout(800)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            "零售-022 Bug：单价 -1 保存成功，弹窗关闭"
        )
        messages = " ".join(page.get_messages())
        assert any(word in messages for word in ("单价", "金额", "不能为负", ">=0")), (
            f"零售-022 负数单价未出现预期提示：{messages}"
        )

    @allure.story("零售-023 新增零售退货并保存")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_023_save_return(
        self,
        retail_back_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_back_page
        page.open_add_modal()
        return_no = page.get_form_number()
        assert return_no.startswith("LSTH"), f"零售-023 单号前缀异常：{return_no}"
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(1)
        page.fill_received_amount(1)
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["retail_return_no"] = return_no
        assert page.get_status(return_no) == "未审核", f"零售-023 {return_no} 状态不是未审核"

    @allure.story("零售-024 关联零售出库单生成退货")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_024_link_sale_return(
        self,
        retail_back_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_back_page
        sale_no = flow_data["audited_retail_sale_no"]
        page.open_retail_back()
        page.open_add_modal()
        return_no = page.get_form_number()
        page.link_bill("linkNumber", sale_no, bill_mode="check")
        row_text = page.get_editable_rows().first.inner_text()
        assert "西瓜2号" in row_text or "1033" in row_text, (
            f"零售-024 退货明细未带出商品：{row_text[:200]}"
        )
        page.fill_received_amount(1)
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["linked_retail_return_no"] = return_no
        assert page.get_status(return_no) == "未审核", f"零售-024 {return_no} 状态不是未审核"

    @allure.story("零售-025 退货数量超过可退数量")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_025_over_return(
        self,
        retail_out_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_out_page
        # 准备一张数量 1、已审核的出库单
        page.open_retail_out()
        page.open_add_modal()
        sale_no = page.get_form_number()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(1)
        page.fill_received_amount(1)
        page.click_save_and_audit()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        assert page.get_status(sale_no) == "已审核", f"零售-025 {sale_no} 未审核"

        page.open_retail_back()
        page.open_add_modal()
        page.link_bill("linkNumber", sale_no, bill_mode="check")
        page.fill_editable_quantity(2)
        page.click_save_only()
        page.page.wait_for_timeout(900)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            "零售-025 超可退数量后弹窗关闭"
        )
        messages = " ".join(page.get_messages())
        assert any(word in messages for word in ("不能大于", "可退", "请修改", "数量")), (
            f"零售-025 超可退数量提示异常：{messages}"
        )
        page.close_modal()

    @allure.story("零售-026 无商品明细退货被拦截")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_026_empty_return_rejected(self, retail_back_page: RetailPage) -> None:
        page = retail_back_page
        before = page.get_row_count()
        page.open_add_modal()
        page.click_save_only()
        page.page.wait_for_timeout(600)
        assert page.page.locator(".ant-modal:visible").count() > 0, "零售-026 无商品仍关闭弹窗"
        assert page.get_row_count() == before, "零售-026 无商品仍新增列表记录"
        page.close_modal()

    @allure.story("零售-027 付款金额与找零")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_027_return_change(
        self,
        retail_back_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_back_page
        page.open_add_modal()
        return_no = page.get_form_number()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(1)
        page.fill_editable_price(10)
        page.fill_received_amount(20)
        assert page.get_change_amount() in {"10", "10.00"}, (
            f"零售-027 找零应为 10，实际 {page.get_change_amount()}"
        )
        page.click_save_only()
        page.wait_modal_closed()
        page.page.wait_for_timeout(800)
        flow_data["retail_change_return_no"] = return_no
        row_text = page.get_list_row(return_no).inner_text()
        assert "20" in row_text and "10" in row_text, f"零售-027 列表值异常：{row_text[:300]}"

    @allure.story("零售-028 退货审核与反审核")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_028_audit_return(
        self,
        retail_back_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_back_page
        return_no = flow_data["retail_return_no"]
        page.open_retail_back()
        page.audit(return_no)
        assert page.get_status(return_no) == "已审核", f"零售-028 {return_no} 审核后不是已审核"
        page.unaudit(return_no)
        assert page.get_status(return_no) == "未审核", f"零售-028 {return_no} 反审核后不是未审核"

    @allure.story("零售-029 已审核退货单不可编辑/删除")
    @pytest.mark.e2e
    @pytest.mark.web
    def test_retail_029_audited_return_readonly(
        self,
        retail_back_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_back_page
        return_no = flow_data["linked_retail_return_no"]
        page.open_retail_back()
        page.audit(return_no)
        assert page.get_status(return_no) == "已审核", f"零售-029 {return_no} 未审核"
        page.click_row_action(return_no, "编辑")
        messages = " ".join(page.get_messages())
        assert "反审核" in messages or "未审核" in messages, f"零售-029 编辑提示异常：{messages}"
        page.click_row_action(return_no, "删除")
        messages = " ".join(page.get_messages())
        assert "反审核" in messages or "未审核" in messages, f"零售-029 删除提示异常：{messages}"

    @allure.story("零售-030 退货按单号查询与重置")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_030_search_return(
        self,
        retail_back_page: RetailPage,
        flow_data: dict,
    ) -> None:
        page = retail_back_page
        return_no = flow_data["retail_return_no"]
        page.open_retail_back()
        page.search_by_number(return_no)
        assert page.get_list_row(return_no).is_visible(), f"零售-030 未查询到 {return_no}"
        page.reset_search()
        assert page.get_row_count() > 0, "零售-030 重置后列表为空"

    @allure.story("零售-031 退货数量 0")
    @allure.description("Bug 预期：退货数量 0 应被拦截；实际若保存成功则为缺陷。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_031_return_quantity_zero(self, retail_back_page: RetailPage) -> None:
        page = retail_back_page
        self._open_sale_with_values(page, "0", "1")
        page.click_save_only()
        page.page.wait_for_timeout(800)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            "零售-031 Bug：退货数量 0 保存成功，弹窗关闭"
        )
        messages = " ".join(page.get_messages())
        assert any(word in messages for word in ("数量", ">=1", "大于0")), (
            f"零售-031 退货数量 0 未出现预期提示：{messages}"
        )

    @allure.story("零售-032 退货数量为负数")
    @allure.description("Bug 预期：退货负数数量应被拦截；实际若保存成功则为缺陷。")
    @pytest.mark.regression
    @pytest.mark.web
    def test_retail_032_return_quantity_negative(
        self,
        retail_back_page: RetailPage,
    ) -> None:
        page = retail_back_page
        self._open_sale_with_values(page, "-1", "1")
        page.click_save_only()
        page.page.wait_for_timeout(800)
        assert page.page.locator(".ant-modal:visible").count() > 0, (
            "零售-032 Bug：退货数量 -1 保存成功，弹窗关闭"
        )
        messages = " ".join(page.get_messages())
        assert any(word in messages for word in ("数量", "大于0", ">=1")), (
            f"零售-032 退货负数数量未出现预期提示：{messages}"
        )

    def _open_sale_with_values(
        self,
        page: RetailPage,
        quantity: str,
        price: str,
        received: str = "0",
    ) -> None:
        page.open_add_modal()
        page.open_product_selector(0)
        page.choose_product("西瓜2号", mode="dblclick")
        page.fill_editable_quantity(quantity)
        page.fill_editable_price(price)
        page.fill_received_amount(received)
