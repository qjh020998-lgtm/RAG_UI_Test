import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from data_loader import load_config, load_login_cases
from page.login_page import LoginPage
from page.purchase_page import PurchasePage
from page.retail_page import RetailPage
from pw_core.base_page import BasePage
from pw_core.driver.browser_session import BrowserSession
from pw_core.driver.context_driver import ContextDriver
from pw_core.driver.driver_manager import DriverManager
from utils.logger import get_logger
from dotenv import load_dotenv
load_dotenv()

log = get_logger(__name__)

AUTH_DIR = Path(__file__).resolve().parent / "artifacts" / "auth"
HOME_PATH = "/dashboard/analysis"
HOME_URL_RE = re.compile(r".*/dashboard/analysis.*")


# -- pytest hook: rep_call available in fixtures --
@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)

    if rep.when == "call":
        if rep.failed:
            last_line = rep.longreprtext.strip().splitlines()[-1]
            log.error(f"ASSERT FAILED | {item.name} | {last_line}")
        else:
            log.info(f"{rep.outcome.upper()} | {item.name} | {rep.duration:.2f}s")
    elif rep.failed:
        last_line = rep.longreprtext.strip().splitlines()[-1]
        log.error(f"{rep.when.upper()} FAILED | {item.name} | {last_line}")


def pytest_runtest_setup(item):
    log.info(f"activating {item.name}")


def pytest_assertion_pass(item, lineno, orig, expl):
    expression = orig.strip().replace("\n", " ")
    log.info(f"ASSERT PASSED | {item.name}:{lineno} | {expression}")


def pytest_collection_modifyitems(config, items):
    """保证采购流程按 请购单 -> 订单 -> 入库 -> 退货 顺序执行。"""
    order = {
        "test_purchase_apply_flow": 0,
        "test_purchase_quantity_validation": 1,
        "test_purchase_order_flow": 2,
        "test_purchase_in_flow": 3,
        "test_purchase_in_multi_account_and_debt_pending": 4,
        "test_purchase_back_flow": 5,
    }

    def key(item):
        if getattr(item, "module", None) and item.module.__name__ == "testcases.test_purchase":
            base_name = item.name.split("[")[0]
            return (1, order.get(base_name, 99))
        return (0, 0)

    items.sort(key=key)


def _auth_snapshot_path(request) -> Path:
    """按测试类生成快照文件名。"""
    safe_name = re.sub(r"[^0-9A-Za-z_.-]+", "_", request.node.nodeid).strip("_")
    return AUTH_DIR / f"{safe_name}.json"


def _is_home(page: Page) -> bool:
    """判断当前页面是否已经登录并进入 ERP 首页。"""
    if "/dashboard/analysis" not in page.url:
        return False
    try:
        page.locator(".ant-menu:visible").first.wait_for(
            state="visible",
            timeout=3000,
        )
        return True
    except Exception:
        return False


def _ensure_home(page: Page, auth_config: dict) -> None:
    """登录并进入首页；已登录在首页时直接返回。"""
    if _is_home(page):
        return

    base_url = auth_config["base_url"]
    home_url = f"{base_url}{HOME_PATH}"
    page.goto(home_url, wait_until="domcontentloaded")
    try:
        page.wait_for_url(HOME_URL_RE, timeout=15000)
    except Exception:
        pass

    if _is_home(page):
        return

    # 快照可能已过期：先清掉旧 localStorage/cookies，避免路由守卫
    # 因为仍有 Login_userId 而把 /user/login 重定向回首页。
    try:
        page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
        page.context.clear_cookies()
        log.info("旧登录态失效，已清理 localStorage/cookies")
    except Exception as exc:
        log.warning(f"清理旧登录态失败：{exc}")

    # 快照失效或没有快照：执行一次 UI 登录
    case = auth_config["case"]
    login_page = LoginPage()
    login_page.goto_login()
    login_page.login_and_wait(
        case.username,
        case.password,
        expect_success=True,
        timeout=60000,
    )
    login_page.assert_login_success()
    expect(page).to_have_url(HOME_URL_RE, timeout=60000)
    page.locator(".ant-menu:visible").first.wait_for(
        state="visible",
        timeout=60000,
    )

    # 登录成功后刷新快照，供当前类后续复用/异常恢复
    state_path = auth_config["state_path"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    page.context.storage_state(path=str(state_path))
    log.info(f"测试类登录快照已保存：{state_path}")


# -- 测试类级配置 --
@pytest.fixture(scope="class")
def auth_config(request) -> dict:
    return {
        "base_url": load_config().base_url.rstrip("/"),
        "case": next(c for c in load_login_cases() if c.expect_success),
        "state_path": _auth_snapshot_path(request),
    }


# -- 测试类级浏览器 --
@pytest.fixture(scope="class")
def browser_session():
    session = BrowserSession().start()
    yield session
    session.close()


# -- 测试类级 context/page：整个类共用同一个页面 --
@pytest.fixture(scope="class")
def class_context(browser_session, auth_config, request):
    state_path = auth_config["state_path"]
    storage_state = str(state_path) if state_path.exists() else None
    context = browser_session.new_context(
        auth_config["base_url"],
        storage_state=storage_state,
    )

    trace_enabled = os.getenv("PW_TRACE", "").lower() in {"1", "true", "yes"}
    trace_path = Path("artifacts/traces") / (
        re.sub(r"[^0-9A-Za-z_.-]+", "_", request.node.nodeid).strip("_") + ".zip"
    )
    if trace_enabled:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        context.tracing.start(screenshots=True, snapshots=True, sources=True)

    yield context

    if trace_enabled:
        try:
            context.tracing.stop(path=str(trace_path))
            log.info(f"Playwright trace 已保存：{trace_path}")
        except Exception as exc:
            log.warning(f"trace 保存失败：{exc}")
    try:
        context.close()
    except Exception:
        pass


@pytest.fixture(scope="class")
def class_page(class_context):
    page = class_context.new_page()
    yield page
    try:
        page.close()
    except Exception:
        pass


@pytest.fixture(scope="class")
def class_driver(class_context, class_page, auth_config, cleanup_registry: set[str]):
    """整个测试类共用的 driver/page；类结束时清理已登记模块。"""
    driver = ContextDriver(class_context, class_page)
    DriverManager().set_driver(driver)
    _ensure_home(class_page, auth_config)
    yield driver

    if cleanup_registry:
        try:
            _cleanup_registered_modules(class_page, cleanup_registry)
        except Exception as exc:
            log.warning(f"[cleanup] 类级清理异常，已忽略：{exc}")


# -- 用例级 driver：登录类用例走 fresh context，其余复用类级 page --
@pytest.fixture()
def driver(request, browser_session, auth_config):
    if request.node.get_closest_marker("no_auth") is not None:
        context = browser_session.new_context(auth_config["base_url"])
        page = context.new_page()
        fresh_driver = ContextDriver(context, page)
        DriverManager().set_driver(fresh_driver)
        try:
            yield fresh_driver
        finally:
            fresh_driver.quit()
        return

    shared_driver = request.getfixturevalue("class_driver")
    DriverManager().set_driver(shared_driver)
    yield shared_driver


# -- 每条用例前后：确保在首页 / 回到首页 --
@pytest.fixture(autouse=True)
def home_guard(request, driver, auth_config):
    if request.node.get_closest_marker("no_auth") is not None:
        yield
        return

    _ensure_home(driver.page, auth_config)
    yield

    home_url = f"{auth_config['base_url']}{HOME_PATH}"
    try:
        BasePage().back_to_home(home_url)
    except Exception as exc:
        log.warning(f"用例后置返回首页失败，尝试重新加载首页：{exc}")
        try:
            driver.page.goto(home_url, wait_until="domcontentloaded")
            driver.page.wait_for_url(HOME_URL_RE, timeout=60000)
            driver.page.locator(".ant-menu:visible").first.wait_for(
                state="visible",
                timeout=60000,
            )
        except Exception:
            raise


# -- Raw Playwright page --
@pytest.fixture()
def pw_page(driver):
    return driver.page


# -- Screenshot on failure (Allure) --
@pytest.fixture(autouse=True)
def screenshot_on_failure(request, driver, home_guard):
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


@pytest.fixture()
def logged_in_page(home_guard, pw_page: Page) -> Page:
    """已登录用例直接复用类级 page；home_guard 已确保首页。"""
    expect(pw_page).to_have_url(HOME_URL_RE, timeout=10000)
    return pw_page


# -- 跨模块流程数据：单证号、单据状态等 --
@pytest.fixture(scope="class")
def flow_data() -> dict:
    return {}


# -- 类级清理：page fixture 只登记，类结束时统一清理 --
CLEANUP_ORDER = (
    "purchase_back",
    "purchase_in",
    "purchase_order",
    "purchase_apply",
    "retail_back",
    "retail_out",
)


def _cleanup_registered_modules(page: Page, modules: set[str]) -> None:
    """按下游到上游顺序清理今日已审核/未审核数据；失败只告警。"""
    if os.getenv("CLEANUP_ENABLED", "1").lower() in {"0", "false", "no"}:
        return
    dry_run = os.getenv("CLEANUP_DRY_RUN", "0").lower() in {"1", "true", "yes"}
    cleaner = PurchasePage()
    errors: list[str] = []
    for module in CLEANUP_ORDER:
        if module not in modules:
            continue
        try:
            cleaner.cleanup_module(module, dry_run=dry_run)
        except Exception as exc:
            message = f"{module}: {exc}"
            errors.append(message)
            log.warning(f"[cleanup] 模块清理失败，已跳过：{message}")
    if errors:
        log.warning(f"[cleanup] 清理失败汇总：{errors}")


@pytest.fixture(scope="class")
def cleanup_registry() -> set[str]:
    return set()


@pytest.fixture()
def purchase_apply_page(logged_in_page: Page, cleanup_registry: set[str]) -> PurchasePage:
    """已登录并打开请购单列表。"""
    cleanup_registry.add("purchase_apply")
    return PurchasePage().open_purchase_apply()


@pytest.fixture()
def purchase_page(logged_in_page: Page, cleanup_registry: set[str]) -> PurchasePage:
    """已登录并打开采购订单列表。"""
    cleanup_registry.add("purchase_order")
    return PurchasePage().open_purchase_order()


@pytest.fixture()
def purchase_in_page(logged_in_page: Page, cleanup_registry: set[str]) -> PurchasePage:
    """已登录并打开采购入库列表。"""
    cleanup_registry.add("purchase_in")
    return PurchasePage().open_purchase_in()


@pytest.fixture()
def purchase_back_page(logged_in_page: Page, cleanup_registry: set[str]) -> PurchasePage:
    """已登录并打开采购退货列表。"""
    cleanup_registry.add("purchase_back")
    return PurchasePage().open_purchase_back()


# -- Retail page fixtures --
@pytest.fixture()
def retail_out_page(logged_in_page: Page, cleanup_registry: set[str]) -> RetailPage:
    """已登录并打开零售出库列表。"""
    cleanup_registry.add("retail_out")
    return RetailPage().open_retail_out()


@pytest.fixture()
def retail_back_page(logged_in_page: Page, cleanup_registry: set[str]) -> RetailPage:
    """已登录并打开零售退货列表。"""
    cleanup_registry.add("retail_back")
    return RetailPage().open_retail_back()
