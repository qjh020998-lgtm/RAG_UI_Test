"""Browser action wrappers for Playwright."""

from pathlib import Path
from playwright.sync_api import Page, Locator, expect

from tools.playwright.conf import PlaywrightConfig


class BrowserActions:
    """浏览器操作封装。
    
    提供常用的页面操作方法，包含等待、重试和错误处理。
    """
    
    def __init__(self, page: Page):
        self.page = page
    
    def click(
        self,
        locator: Locator,
        timeout: int | None = None,
        force: bool = False
    ) -> None:
        """点击元素。
        
        Args:
            locator: 目标元素的 Locator
            timeout: 超时时间（毫秒），默认使用页面超时设置
            force: 是否强制点击（跳过可操作检查）
        """
        locator.click(timeout=timeout, force=force)
    
    def double_click(
        self,
        locator: Locator,
        timeout: int | None = None
    ) -> None:
        """双击元素。"""
        locator.dblclick(timeout=timeout)
    
    def fill(
        self,
        locator: Locator,
        value: str,
        timeout: int | None = None
    ) -> None:
        """填写输入框（会先清空内容）。
        
        Args:
            locator: 输入框的 Locator
            value: 要填入的值
            timeout: 超时时间（毫秒）
        """
        locator.fill(value, timeout=timeout)
    
    def type_text(
        self,
        locator: Locator,
        value: str,
        delay: int = 0,
        timeout: int | None = None
    ) -> None:
        """逐字输入文本（不清空现有内容）。
        
        Args:
            locator: 输入框的 Locator
            value: 要输入的文本
            delay: 每个字符间的延迟（毫秒）
            timeout: 超时时间（毫秒）
        """
        locator.type(value, delay=delay, timeout=timeout)
    
    def clear(self, locator: Locator, timeout: int | None = None) -> None:
        """清空输入框。"""
        locator.fill("", timeout=timeout)
    
    def select_option(
        self,
        locator: Locator,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
        timeout: int | None = None
    ) -> None:
        """选择下拉框选项。
        
        Args:
            locator: select 元素的 Locator
            value: option 的 value 属性
            label: option 的显示文本
            index: option 的索引（从0开始）
            timeout: 超时时间（毫秒）
        """
        if value is not None:
            locator.select_option(value=value, timeout=timeout)
        elif label is not None:
            locator.select_option(label=label, timeout=timeout)
        elif index is not None:
            locator.select_option(index=index, timeout=timeout)
    
    def check(self, locator: Locator, timeout: int | None = None) -> None:
        """勾选复选框。"""
        locator.check(timeout=timeout)
    
    def uncheck(self, locator: Locator, timeout: int | None = None) -> None:
        """取消勾选复选框。"""
        locator.uncheck(timeout=timeout)
    
    def get_text(self, locator: Locator, timeout: int | None = None) -> str:
        """获取元素的文本内容。
        
        Args:
            locator: 目标元素的 Locator
            timeout: 超时时间（毫秒）
            
        Returns:
            元素的文本内容
        """
        return locator.text_content(timeout=timeout) or ""
    
    def get_inner_text(
        self,
        locator: Locator,
        timeout: int | None = None
    ) -> str:
        """获取元素的 innerText。"""
        return locator.inner_text(timeout=timeout)
    
    def get_input_value(
        self,
        locator: Locator,
        timeout: int | None = None
    ) -> str:
        """获取输入框的值。"""
        return locator.input_value(timeout=timeout)
    
    def get_attribute(
        self,
        locator: Locator,
        name: str,
        timeout: int | None = None
    ) -> str | None:
        """获取元素的属性值。"""
        return locator.get_attribute(name, timeout=timeout)
    
    def is_visible(self, locator: Locator) -> bool:
        """检查元素是否可见。"""
        return locator.is_visible()
    
    def is_enabled(self, locator: Locator) -> bool:
        """检查元素是否启用。"""
        return locator.is_enabled()
    
    def is_checked(self, locator: Locator) -> bool:
        """检查复选框是否被勾选。"""
        return locator.is_checked()
    
    def is_disabled(self, locator: Locator) -> bool:
        """检查元素是否禁用。"""
        return locator.is_disabled()
    
    def wait_for_load_state(
        self,
        state: str = "networkidle",
        timeout: int | None = None
    ) -> None:
        """等待页面加载状态。
        
        Args:
            state: 加载状态，可选值：
                - 'load': 等待 load 事件
                - 'domcontentloaded': 等待 DOMContentLoaded 事件
                - 'networkidle': 等待网络空闲（推荐）
            timeout: 超时时间（毫秒）
        """
        self.page.wait_for_load_state(state, timeout=timeout)
    
    def wait_for_url(
        self,
        url: str | None = None,
        timeout: int | None = None
    ) -> None:
        """等待 URL 变化。
        
        Args:
            url: 目标 URL（支持 glob 模式）
            timeout: 超时时间（毫秒）
        """
        if url:
            self.page.wait_for_url(url, timeout=timeout)
    
    def wait_for_selector(
        self,
        locator: Locator,
        state: str = "visible",
        timeout: int | None = None
    ) -> None:
        """等待元素出现。
        
        Args:
            locator: 目标元素的 Locator
            state: 等待状态，可选值：
                - 'attached': 元素已附加到 DOM
                - 'detached': 元素已从 DOM 移除
                - 'visible': 元素可见
                - 'hidden': 元素隐藏
            timeout: 超时时间（毫秒）
        """
        locator.wait_for(state=state, timeout=timeout)
    
    def navigate(self, url: str, wait_until: str = "load") -> None:
        """导航到指定 URL。
        
        Args:
            url: 目标 URL
            wait_until: 等待条件，可选值：
                - 'load': 等待 load 事件
                - 'domcontentloaded': 等待 DOMContentLoaded
                - 'networkidle': 等待网络空闲
        """
        self.page.goto(url, wait_until=wait_until)
    
    def take_screenshot(
        self,
        name: str,
        full_page: bool = False
    ) -> str:
        """截取页面截图。
        
        Args:
            name: 截图文件名（不含扩展名）
            full_page: 是否截取完整页面
            
        Returns:
            截图文件的完整路径
        """
        screenshot_dir = Path(PlaywrightConfig().screenshot_dir)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = screenshot_dir / f"{name}.png"
        self.page.screenshot(path=str(file_path), full_page=full_page)
        
        return str(file_path.absolute())
    
    def get_url(self) -> str:
        """获取当前页面 URL。"""
        return self.page.url
    
    def get_title(self) -> str:
        """获取当前页面标题。"""
        return self.page.title()
    
    def press_key(self, locator: Locator, key: str) -> None:
        """在元素上按键。
        
        Args:
            locator: 目标元素的 Locator
            key: 按键名称，如 'Enter', 'Tab', 'Escape'
        """
        locator.press(key)
    
    def hover(self, locator: Locator) -> None:
        """鼠标悬停在元素上。"""
        locator.hover()
    
    def focus(self, locator: Locator) -> None:
        """聚焦到元素。"""
        locator.focus()
    
    def scroll_into_view(self, locator: Locator) -> None:
        """滚动元素到可视区域。"""
        locator.scroll_into_view_if_needed()
    
    def assert_visible(self, locator: Locator, timeout: int = 5000) -> None:
        """断言元素可见。"""
        expect(locator).to_be_visible(timeout=timeout)
    
    def assert_hidden(self, locator: Locator, timeout: int = 5000) -> None:
        """断言元素隐藏。"""
        expect(locator).to_be_hidden(timeout=timeout)
    
    def assert_enabled(self, locator: Locator, timeout: int = 5000) -> None:
        """断言元素启用。"""
        expect(locator).to_be_enabled(timeout=timeout)
    
    def assert_disabled(self, locator: Locator, timeout: int = 5000) -> None:
        """断言元素禁用。"""
        expect(locator).to_be_disabled(timeout=timeout)
    
    def assert_text(
        self,
        locator: Locator,
        expected: str,
        timeout: int = 5000
    ) -> None:
        """断言元素包含指定文本。"""
        expect(locator).to_contain_text(expected, timeout=timeout)
    
    def assert_value(
        self,
        locator: Locator,
        expected: str,
        timeout: int = 5000
    ) -> None:
        """断言输入框包含指定值。"""
        expect(locator).to_have_value(expected, timeout=timeout)
