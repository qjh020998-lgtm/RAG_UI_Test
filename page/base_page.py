"""Base Page Object class."""

from playwright.sync_api import Page
from tools.playwright.locators import A11yLocator
from tools.playwright.actions import BrowserActions


class BasePage:
    """所有 Page Object 的基类。
    
    提供通用的页面操作能力和工具访问。
    
    Attributes:
        page: Playwright Page 对象
        locator: A11y 定位器帮助类
        actions: 浏览器操作封装
    """
    
    def __init__(self, page: Page):
        self.page = page
        self.locator = A11yLocator(page)
        self.actions = BrowserActions(page)
    
    def navigate(self, url: str, wait_until: str = "load") -> None:
        """导航到指定 URL。
        
        Args:
            url: 目标 URL
            wait_until: 等待条件
        """
        self.actions.navigate(url, wait_until=wait_until)
    
    def get_url(self) -> str:
        """获取当前页面 URL。"""
        return self.actions.get_url()
    
    def get_title(self) -> str:
        """获取当前页面标题。"""
        return self.actions.get_title()
    
    def wait_for_load(self, state: str = "networkidle") -> None:
        """等待页面加载完成。
        
        Args:
            state: 加载状态
        """
        self.actions.wait_for_load_state(state)
    
    def take_screenshot(self, name: str, full_page: bool = False) -> str:
        """截取页面截图。
        
        Args:
            name: 截图文件名
            full_page: 是否截取完整页面
            
        Returns:
            截图文件路径
        """
        return self.actions.take_screenshot(name, full_page=full_page)
    
    def is_on_page(self, url_fragment: str) -> bool:
        """检查当前是否在指定页面上。
        
        Args:
            url_fragment: URL 片段
            
        Returns:
            是否在指定页面
        """
        return url_fragment in self.get_url()
    
    def reload(self) -> None:
        """重新加载页面。"""
        self.page.reload()
    
    def go_back(self) -> None:
        """浏览器后退。"""
        self.page.go_back()
    
    def go_forward(self) -> None:
        """浏览器前进。"""
        self.page.go_forward()
