"""页面控件搜索工具（编排流程中的「websearch 获取控件」分支）。

严格按 ``plan/Agent/页面控件缓存方案.md`` 的思路实现：

- **L1 A11y 为主**（``use_browser=True`` 默认）：Playwright 打开页面，
  抓 ``aria_snapshot()``，拿到 role + name + locator，最准；
- **L2 JS bundle 补漏**：抓 ``app.js`` 里对应页面的 chunk，
  正则提取中文字符串，过滤噪声后作为控件名补充（role=unknown）；
- **L3 websearch 只补业务**：由调用方传入 ``business``，本工具不搜索；
- 缓存按 ``module/page/state`` 分文件，带 ``bundle_hash`` 用于失效判断。

注意
----
- web_search（DeepSeek / DuckDuckGo）**不能**替代本工具：它拿不到 DOM/A11y。
- 本工具只读站点、只写 ``cache/pages/``，不修改任何业务代码。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://39.101.122.147:3000"
CACHE_ROOT = Path(os.getenv("RAG_CACHE_DIR", "cache")) / "pages"

# 页面名 -> 前端 chunk 关键词（ERP 升级后如果文件名变了，需要更新这里）
PAGE_CHUNK_KEYWORDS: dict[str, str] = {
    "采购订单": "PurchaseOrder",
    "采购入库": "PurchaseIn",
    "采购退货": "PurchaseBack",
    "请购单": "PurchaseApply",
    "零售出库": "RetailOut",
    "零售退货": "RetailBack",
}

# 噪声过滤：这些不是控件名，是提示语/校验语
NOISE_PREFIX = ("请输入", "请选择", "请扫描", "请填写", "请上传", "请确认")
NOISE_SUFFIX = ("不能为空", "失败", "错误", "成功", "请重试", "已完成")
NOISE_PATTERN = re.compile(r"[！？。，；：]|已经|不能|请先|请勿")
MAX_NAME_LENGTH = 12

# 这些角色是结构/数据，不是控件；保留它们只会让清单变脏
SKIP_ROLES = {"row", "cell", "text", "document", "generic", "none", "presentation"}

# 页面状态 -> 打开方式（create_modal 需要先点"新增"）
MODAL_STATES = {"create_modal"}


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------


def _cache_path(module: str, page: str, state: str) -> Path:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return CACHE_ROOT / f"{module}_{page}_{state}.json"


def _load_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# L1：A11y 树（需要浏览器）
# ---------------------------------------------------------------------------


def _open_and_snapshot(url: str, state: str, storage_state: str = "") -> str:
    """打开页面（必要时点开弹窗），返回 aria snapshot 文本。

    ``storage_state`` 是 Playwright 的登录快照 JSON，用它复用登录态，
    否则会停在登录页。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=storage_state or None)
        page = context.new_page()
        try:
            for attempt in range(2):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    break
                except Exception:
                    if attempt == 1:
                        raise
                    page.wait_for_timeout(2000)
            # ERP 是 SPA 且有轮询请求，networkidle 可能永远不触发；
            # 等关键容器 + 短暂停留，让表格/弹窗完成渲染
            page.wait_for_selector(".ant-layout, #app", timeout=15000)
            page.wait_for_timeout(1500)

            target = page.locator("body")
            if state in MODAL_STATES:
                # 新手引导遮罩会拦截“新增”点击，先移除
                page.evaluate(
                    "() => document.querySelectorAll('.introjs-overlay,.introjs-tooltip,"
                    ".introjs-helperLayer,.introjs-disableInteraction').forEach(n => n.remove())"
                )
                trigger = page.get_by_role("button", name="新增").first
                trigger.click()
                page.wait_for_selector(".ant-modal", state="visible", timeout=10000)
                page.wait_for_timeout(800)
                # 新增表单可能是整页，也可能是弹窗；直接抓整个页面，避免漏控件
                target = page.locator("body")

            # Playwright 1.49+ 用 aria_snapshot；旧版退回 accessibility
            if hasattr(target, "aria_snapshot"):
                return target.aria_snapshot()
            snapshot = page.accessibility.snapshot()
            return json.dumps(snapshot, ensure_ascii=False)
        finally:
            browser.close()


def _clean_name(name: str) -> str:
    """清洗 A11y 名称：去图标前缀、图标名、必填星号、冒号、空格。

    ``图标: plus 插入行`` -> ``插入行``
    ``* 供应商:``       -> ``供应商``
    ``取 消``           -> ``取消``
    """
    cleaned = name.replace("图标:", "").strip()
    cleaned = re.sub(r"^[a-z][a-z-]*\s+", "", cleaned)  # 去图标名（plus/history/...）
    cleaned = re.sub(r"^(请选择|请输入|请填写)", "", cleaned)  # 去 placeholder 前缀
    cleaned = re.sub(r"^[*\s]+", "", cleaned)  # 去必填星号
    cleaned = re.sub(r"[:：]$", "", cleaned)  # 去冒号
    cleaned = re.sub(r"\s+", "", cleaned)  # 去空格
    return cleaned


_ICON_ONLY = re.compile(r"^图标:\s*([a-z0-9-]+)$")


def _icon_control(raw_name: str) -> dict | None:
    """A11y 把 Ant Design 图标报成 button，但 DOM 里是 <i>，role 定位必然失败。"""
    match = _ICON_ONLY.match((raw_name or "").strip())
    if not match:
        return None
    icon = match.group(1)
    return {
        "name": icon,
        "raw_name": raw_name,
        "role": "icon",
        "locator": f"i.anticon-{icon}",
        "source": "a11y-icon",
    }


def _parse_aria(snapshot: str) -> list[dict]:
    """把 aria snapshot（YAML 或 JSON）转成控件列表。

    ``name`` 是清洗后的名称（用于展示/合并），``raw_name`` 保留原始名称
    （locator 必须用原始名称，否则 ``get_by_role`` 可能匹配不到）。
    """
    controls: list[dict] = []

    # 1) YAML 形式，两种写法：
    #    - button "新增"
    #    - combobox: 请选择供应商
    for line in snapshot.splitlines():
        # 兼容 YAML 引号包裹：- 'button "图标: plus 插入行"': 插入行
        match = re.match(r"""\s*-\s*['"]?([a-zA-Z]+)\s+["']([^"']+)["']""", line)
        if not match:
            match = re.match(r"""\s*-\s*['"]?([a-zA-Z]+)\s*:\s*(.+)$""", line)
        if not match:
            continue
        role = match.group(1)
        raw_name = match.group(2).strip().strip('"').strip("'")
        icon = _icon_control(raw_name) if role == "button" else None
        if icon:
            controls.append(icon)
            continue
        clean = _clean_name(raw_name)
        if not clean or clean in {"body", "document"}:
            continue
        if role in SKIP_ROLES or len(clean) > MAX_NAME_LENGTH:
            continue
        if clean.isdigit():  # 输入框里的值，不是控件名
            continue
        controls.append(
            {
                "name": clean,
                "raw_name": raw_name,
                "role": role,
                "locator": f"get_by_role('{role}', name='{raw_name}')",
                "source": "a11y",
            }
        )
    if controls:
        return controls

    # 2) JSON 形式（旧版 accessibility.snapshot）
    try:
        tree = json.loads(snapshot)
    except json.JSONDecodeError:
        return []

    def walk(node: dict) -> None:
        role = node.get("role", "")
        raw_name = (node.get("name") or "").strip()
        icon = _icon_control(raw_name) if role == "button" else None
        if icon:
            controls.append(icon)
            for child in node.get("children", []) or []:
                if isinstance(child, dict):
                    walk(child)
            return
        clean = _clean_name(raw_name)
        if role and clean and role not in {"WebArea", "RootWebArea"}:
            if role not in SKIP_ROLES and len(clean) <= MAX_NAME_LENGTH:
                controls.append(
                    {
                        "name": clean,
                        "raw_name": raw_name,
                        "role": role,
                        "locator": f"get_by_role('{role}', name='{raw_name}')",
                        "source": "a11y",
                    }
                )
        for child in node.get("children", []) or []:
            if isinstance(child, dict):
                walk(child)

    if isinstance(tree, dict):
        walk(tree)
    return controls


def _fetch_a11y(url: str, state: str, storage_state: str = "") -> list[dict]:
    if not url:
        return []
    try:
        snapshot = _open_and_snapshot(url, state, storage_state)
    except ImportError:
        log.warning("未安装 playwright，跳过 A11y 抓取")
        return []
    except Exception as exc:  # noqa: BLE001 - 工具不应因抓取失败而崩溃
        log.warning("A11y 抓取失败（state=%s）：%s", state, exc)
        return []
    return _parse_aria(snapshot)


# ---------------------------------------------------------------------------
# L2：前端 JS bundle
# ---------------------------------------------------------------------------


def _base_url() -> str:
    return os.getenv("BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _fetch_js_strings(page: str) -> tuple[list[str], str]:
    """返回 ``(中文字符串列表, bundle 哈希)``；失败返回 ``([], "")``。

    Vue CLI 的加载链：``login.html`` -> ``app.js``（路由表 + chunk 映射）
    -> ``/js/chunk-xxx.<hash>.js``（页面自己的 bundle）。
    """
    keyword = PAGE_CHUNK_KEYWORDS.get(page)
    if not keyword:
        log.warning("页面 %s 没有配置 chunk 关键词，跳过 JS 提取", page)
        return [], ""

    base = _base_url()
    try:
        login_html = httpx.get(f"{base}/user/login", timeout=20, follow_redirects=True).text
        # src 可能没有引号：<script src=/js/app.6db23162.js>
        app_match = re.search(r'src=["\']?([^"\'\s>]*app[^"\'\s>]*\.js)', login_html)
        if not app_match:
            log.warning("登录页没找到 app.js")
            return [], ""

        app_url = urljoin(base + "/", app_match.group(1).lstrip("/"))
        app_body = httpx.get(app_url, timeout=30, follow_redirects=True).text
        bundle_hash = hashlib.md5(app_body.encode("utf-8")).hexdigest()[:6]

        # 路由表："./views/bill/PurchaseOrderList":["e218",9,...,"chunk-f0a96cd6"]
        route = re.search(
            rf'\./views/[^"]*{re.escape(keyword)}[^"]*":\[[^\]]*"([^"]+)"\]',
            app_body,
        )
        if not route:
            log.warning("app.js 里没找到 %s 的路由 chunk", keyword)
            return [], bundle_hash
        chunk_name = route.group(1)

        # chunk 映射：第一个是 JS hash，第二个是 CSS hash
        chunk_hash = re.search(rf'"{re.escape(chunk_name)}":"([a-f0-9]+)"', app_body)
        if not chunk_hash:
            log.warning("app.js 里没找到 %s 的 hash", chunk_name)
            return [], bundle_hash

        chunk_url = f"{base}/js/{chunk_name}.{chunk_hash.group(1)}.js"
        body = httpx.get(chunk_url, timeout=30, follow_redirects=True).text
        strings = sorted(set(re.findall(r'["\']([\u4e00-\u9fa5][^"\']{1,30})["\']', body)))
        return strings, bundle_hash
    except Exception as exc:  # noqa: BLE001
        log.warning("JS bundle 抓取失败：%s", exc)
        return [], ""


def _filter_strings(strings: list[str]) -> list[str]:
    """过滤提示语/模板串，只保留像控件名的短语。"""
    result: list[str] = []
    for raw in strings:
        name = raw.strip()
        if not name or "${" in name:
            continue
        if len(name) > MAX_NAME_LENGTH:
            continue
        if name.startswith(NOISE_PREFIX):
            continue
        if name.endswith(NOISE_SUFFIX):
            continue
        if NOISE_PATTERN.search(name):
            continue
        result.append(name)
    return sorted(set(result))


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------


def _merge(a11y: list[dict], js_names: list[str]) -> list[dict]:
    """按清洗后的名称合并：A11y 优先（带 role），JS 补漏（role=unknown）。"""
    merged: dict[str, dict] = {item["name"]: item for item in a11y}
    for raw in js_names:
        name = _clean_name(raw)
        if name and name not in merged:
            merged[name] = {"name": name, "role": "unknown", "locator": "", "source": "js"}
    return list(merged.values())


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def search_page_elements(
    module: str,
    page: str,
    *,
    state: str = "list",
    url: str = "",
    use_browser: bool = True,
    storage_state: str = "",
    business: str = "",
    refresh: bool = False,
) -> dict:
    """搜索页面控件；命中缓存直接返回，否则抓取后写缓存。

    Args:
        module: 业务模块，如 ``采购管理``
        page: 页面名，如 ``采购订单``
        state: 页面状态，``list`` / ``create_modal`` / ``edit_modal``
        url: 页面 URL，``use_browser=True`` 时必填
        use_browser: 是否用 Playwright 抓 A11y（L1）
        storage_state: 登录快照 JSON 路径（复用登录态，避免停在登录页）
        business: 业务流程描述（L3 由调用方传入，本工具不搜索）
        refresh: 忽略缓存强制重抓

    Returns:
        ``{module, page, state, url, captured_at, bundle_hash, sources, controls, business, cached}``
    """
    path = _cache_path(module, page, state)
    if not refresh:
        cached = _load_cache(path)
        if cached:
            return {**cached, "cached": True}

    a11y = _fetch_a11y(url, state, storage_state) if use_browser else []
    if state in MODAL_STATES and use_browser and not a11y:
        # 抓 A11y 失败时不要用 JS 字符串覆盖已有缓存，否则会丢掉 role/locator
        return {
            "module": module,
            "page": page,
            "state": state,
            "url": url,
            "controls": [],
            "error": "A11y 抓取失败，保留旧缓存",
        }
    js_strings, bundle_hash = _fetch_js_strings(page)
    js_names = _filter_strings(js_strings)
    controls = _merge(a11y, js_names)

    payload: dict = {
        "module": module,
        "page": page,
        "state": state,
        "url": url,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "bundle_hash": bundle_hash,
        "sources": [name for name, value in (("a11y", a11y), ("js", js_names)) if value],
        "controls": controls,
        "business": business,
        "cached": False,
    }

    if not controls:
        payload["error"] = "未抓到控件：页面不在 chunk 映射里，或站点不可达"
        return payload

    _save_cache(path, payload)
    return payload


def capture_all_states(
    module: str,
    page: str,
    url: str,
    *,
    storage_state: str = "",
    business: str = "",
    refresh: bool = True,
) -> dict[str, dict]:
    """一次抓列表页和新增弹窗（对应方案文档 §4 的 capture_page）。"""
    return {
        state: search_page_elements(
            module,
            page,
            state=state,
            url=url,
            use_browser=True,
            storage_state=storage_state,
            business=business,
            refresh=refresh,
        )
        for state in ("list", "create_modal")
    }


# ---------------------------------------------------------------------------
# 工具描述（给编排 Agent 用）
# ---------------------------------------------------------------------------

TOOL_SPEC: dict = {
    "name": "search_page_elements",
    "description": (
        "当页面控件缓存未命中、组件库也没有记录时，用 Playwright 抓 A11y 树（可选 JS bundle 补漏），"
        "提取页面控件清单并写入 cache/pages/。对应编排流程中"
        "「页面组件不存在且组件库无记录 → 搜索页面元素」的分支。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "module": {"type": "string", "description": "业务模块，如 采购管理"},
            "page": {"type": "string", "description": "页面名，如 采购订单"},
            "state": {
                "type": "string",
                "enum": ["list", "create_modal", "edit_modal"],
                "default": "list",
                "description": "页面状态；弹窗控件必须与列表页分开缓存",
            },
            "url": {"type": "string", "description": "页面 URL；use_browser=true 时必填"},
            "storage_state": {
                "type": "string",
                "description": "登录快照 JSON 路径；use_browser=true 时用它复用登录态",
            },
            "use_browser": {
                "type": "boolean",
                "default": True,
                "description": "是否用 Playwright 抓 A11y 树（L1，最准）",
            },
            "business": {
                "type": "string",
                "description": "业务流程描述（L3 由编排器传入，本工具不搜索）",
            },
            "refresh": {"type": "boolean", "default": False, "description": "忽略缓存强制重抓"},
        },
        "required": ["module", "page"],
    },
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="页面控件搜索（缓存 miss 时抓取）")
    parser.add_argument("--module", required=True, help="业务模块，如 采购管理")
    parser.add_argument("--page", required=True, help="页面名，如 采购订单")
    parser.add_argument("--state", default="list", help="list / create_modal / edit_modal")
    parser.add_argument("--url", default="", help="页面 URL，配合 --browser")
    parser.add_argument("--storage-state", default="", help="登录快照 JSON 路径")
    parser.add_argument("--browser", action="store_true", help="用 Playwright 抓 A11y")
    parser.add_argument("--all-states", action="store_true", help="一次抓列表页和新增弹窗")
    parser.add_argument("--business", default="", help="业务流程描述（可选）")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存强制重抓")
    args = parser.parse_args(argv)

    if args.all_states:
        result = capture_all_states(
            args.module,
            args.page,
            args.url,
            storage_state=args.storage_state,
            business=args.business,
            refresh=args.refresh,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if any(item.get("controls") for item in result.values()) else 1

    result = search_page_elements(
        args.module,
        args.page,
        state=args.state,
        url=args.url,
        use_browser=args.browser,
        storage_state=args.storage_state,
        business=args.business,
        refresh=args.refresh,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("controls") else 1


if __name__ == "__main__":
    raise SystemExit(main())
