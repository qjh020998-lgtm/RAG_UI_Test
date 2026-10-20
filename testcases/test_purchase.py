"""采购管理页面 UI 用例（采购订单 / 采购入库 / 采购退货）。"""

import allure
import pytest

from page.purchase_page import PurchasePage


@allure.feature("采购管理")
class TestPurchase:
    """采购管理核心页面与新增弹窗校验。"""

    @allure.story("PUR-001 - 采购订单列表加载")
    @allure.title("采购订单列表加载并展示单据")
    @pytest.mark.smoke
    @pytest.mark.web
    def test_purchase_order_list_loaded(self, purchase_page: PurchasePage) -> None:
        purchase_page.assert_page_loaded("采购订单", "purchase_order")

        row_count = purchase_page.wait_for_list_ready()
        assert row_count > 0, "采购订单列表没有加载出任何单据"

    @allure.story("PUR-002 - 采购订单按单号查询")
    @allure.title("采购订单按列表第一行单号查询")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_order_search_by_number(
        self,
        purchase_page: PurchasePage,
    ) -> None:
        number = purchase_page.get_first_row_number()
        assert number, "未能从采购订单列表第一行读取到单据编号"

        purchase_page.search_by_number(number)
        purchase_page.assert_row_exists(number)

    @allure.story("PUR-003 - 采购订单新增必填校验")
    @allure.title("采购订单新增未选供应商时保存被拦截")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_order_add_modal_validation(
        self,
        purchase_page: PurchasePage,
    ) -> None:
        purchase_page.open_add_modal()
        purchase_page.assert_add_modal_visible(
            "采购订单",
            ["供应商", "单据日期", "单据编号", "结算账户"],
        )
        purchase_page.assert_material_table_visible()

        purchase_page.click_save()
        purchase_page.assert_validation_message("请选择供应商")
        purchase_page.assert_modal_visible()

    @allure.story("PUR-004 - 采购订单状态标签")
    @allure.title("采购订单列表状态标签在允许范围内")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_order_status_tags(
        self,
        purchase_page: PurchasePage,
    ) -> None:
        purchase_page.assert_status_tags(
            {"未审核", "已审核", "完成采购", "部分采购", "审核中"}
        )

    @allure.story("PUR-005 - 采购入库列表加载")
    @allure.title("采购入库列表加载并展示单据")
    @pytest.mark.smoke
    @pytest.mark.web
    def test_purchase_in_list_loaded(self, purchase_in_page: PurchasePage) -> None:
        purchase_in_page.assert_page_loaded("采购入库", "purchase_in")

        row_count = purchase_in_page.wait_for_list_ready()
        assert row_count > 0, "采购入库列表没有加载出任何单据"

    @allure.story("PUR-006 - 采购入库新增弹窗字段")
    @allure.title("采购入库新增弹窗包含仓库、供应商、结算账户")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_in_add_modal_fields(
        self,
        purchase_in_page: PurchasePage,
    ) -> None:
        purchase_in_page.open_add_modal()
        purchase_in_page.assert_add_modal_visible(
            "采购入库",
            ["供应商", "仓库", "单据日期", "单据编号", "结算账户"],
        )
        purchase_in_page.assert_material_table_visible()

    @allure.story("PUR-007 - 采购退货列表加载")
    @allure.title("采购退货列表加载并渲染表格")
    @pytest.mark.smoke
    @pytest.mark.web
    def test_purchase_back_list_loaded(
        self,
        purchase_back_page: PurchasePage,
    ) -> None:
        purchase_back_page.assert_page_loaded("采购退货", "purchase_back")

        # 列表默认按近 3 天过滤，历史数据可能不在默认范围内；
        # 无数据时校验空状态，避免把“默认没数据”误判为页面加载失败。
        if purchase_back_page.wait_for_list_ready() == 0:
            purchase_back_page.assert_empty_state()

    @allure.story("PUR-008 - 采购退货新增弹窗字段")
    @allure.title("采购退货新增弹窗包含关联单据、退款与欠款")
    @pytest.mark.regression
    @pytest.mark.web
    def test_purchase_back_add_modal_fields(
        self,
        purchase_back_page: PurchasePage,
    ) -> None:
        purchase_back_page.open_add_modal()
        purchase_back_page.assert_add_modal_visible(
            "采购退货",
            ["供应商", "仓库", "关联单据", "本次退款", "本次欠款"],
        )
        purchase_back_page.assert_material_table_visible()
