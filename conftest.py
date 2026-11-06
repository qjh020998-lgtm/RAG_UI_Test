"""pytest 全局配置：日志与断言输出。

UI 测试与 POM 驱动（infrastructure/）已移除，这里只保留与框架无关的钩子。
"""
import pytest
from dotenv import load_dotenv

from utils.logger import get_logger

load_dotenv()
log = get_logger(__name__)


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


# 第三方 async/tracing 库内部的 assert，不是测试代码
_INTERNAL_ASSERTIONS = (
    "self._host_task is not None",
    "tracing_queue is not None",
    "task_state.cancel_scope is not None",
    "_task in task_state.cancel_scope._tasks",
)


def pytest_assertion_pass(item, lineno, orig, expl):
    expression = orig.strip().replace("\n", " ")

    if item.get_closest_marker("agent") is not None:
        return
    if any(marker in expression for marker in _INTERNAL_ASSERTIONS):
        return

    log.info(f"ASSERT PASSED | {item.name}:{lineno} | {expression}")
