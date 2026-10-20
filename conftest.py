import pytest
from playwright.sync_api import Page

from data_loader import load_config, load_login_cases
from page.login_page import LoginPage
from page.purchase_page import PurchasePage
from pw_core.driver.driver_manager import DriverManager
from pw_core.driver.web_driver import WebDriver


# -- pytest hook: rep_call available in fixtures --
@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


# -- Driver lifecycle: one browser per test function --
@pytest.fixture()
def driver():
    """Create WebDriver, register in singleton, tear down after test."""
    base_url = load_config().base_url
    web_driver = WebDriver().start(base_url=base_url)
    DriverManager().set_driver(web_driver)
    yield web_driver
    DriverManager().quit_driver()


# -- Raw Playwright page --
@pytest.fixture()
def pw_page(driver):
    return driver.page


# -- Screenshot on failure (Allure) --
@pytest.fixture(autouse=True)
def screenshot_on_failure(request, driver):
    yield
    report = getattr(request.node, "rep_call", None)
    if report is not None and report.failed:
        try:
            import allure
            from utils.config import Config
            if Config.SCREENSHOT_ON_FAILURE:
                shot = driver.take_screenshot(request.node.name)
                allure.attach(
                    shot,
                    name=f"FAIL_{request.node.name}",
                    attachment_type=allure.attachment_type.PNG,
                )
        except Exception:
            pass


# -- Page object fixtures --
@pytest.fixture()
def login_page(pw_page) -> LoginPage:
    return LoginPage()


# -- Authenticated page: 用 YAML 中第一条可成功登录的账号完成登录 --
@pytest.fixture()
def logged_in_page(login_page: LoginPage) -> Page:
    """打开登录页、登录成功并返回已登录的 Page。"""
    case = next(c for c in load_login_cases() if c.expect_success)
    login_page.goto_login()
    login_page.login_and_wait(
        case.username,
        case.password,
        expect_success=True,
        timeout=30000,
    )
    login_page.assert_login_success()
    return login_page.page


# -- Purchase page fixtures --
@pytest.fixture()
def purchase_page(logged_in_page: Page) -> PurchasePage:
    """已登录并打开采购订单列表。"""
    return PurchasePage().open_purchase_order()


@pytest.fixture()
def purchase_in_page(logged_in_page: Page) -> PurchasePage:
    """已登录并打开采购入库列表。"""
    return PurchasePage().open_purchase_in()


@pytest.fixture()
def purchase_back_page(logged_in_page: Page) -> PurchasePage:
    """已登录并打开采购退货列表。"""
    return PurchasePage().open_purchase_back()
