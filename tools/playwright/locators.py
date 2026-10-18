"""Accessibility Tree based locator helpers for Playwright."""

from playwright.sync_api import Page, Locator


class A11yLocator:
    """基于 Accessibility Tree 的定位器封装。
    
    优先使用语义化定位方法（role、text、label等），
    避免使用 CSS 选择器和 XPath。
    """
    
    def __init__(self, page: Page):
        self.page = page
    
    def by_role(
        self,
        role: str,
        name: str | None = None,
        exact: bool | None = None
    ) -> Locator:
        """通过 ARIA role 定位元素。
        
        Args:
            role: ARIA 角色，如 'button', 'textbox', 'heading'
            name: 元素的 accessible name
            exact: 是否精确匹配 name
            
        Returns:
            Locator 对象
            
        Example:
            locator.by_role("button", name="登录")
            locator.by_role("textbox", name="用户名")
        """
        kwargs = {}
        if name is not None:
            kwargs["name"] = name
        if exact is not None:
            kwargs["exact"] = exact
        return self.page.get_by_role(role, **kwargs)
    
    def by_text(self, text: str, exact: bool = False) -> Locator:
        """通过可见文本定位元素。
        
        Args:
            text: 要匹配的文本内容
            exact: 是否精确匹配
            
        Returns:
            Locator 对象
            
        Example:
            locator.by_text("保存")
            locator.by_text("确认删除", exact=True)
        """
        return self.page.get_by_text(text, exact=exact)
    
    def by_label(
        self,
        label: str,
        exact: bool | None = None
    ) -> Locator:
        """通过表单标签定位输入框。
        
        Args:
            label: label 元素的文本
            exact: 是否精确匹配
            
        Returns:
            Locator 对象
            
        Example:
            locator.by_label("用户名")
            locator.by_label("密码")
        """
        kwargs = {}
        if exact is not None:
            kwargs["exact"] = exact
        return self.page.get_by_label(label, **kwargs)
    
    def by_placeholder(
        self,
        placeholder: str,
        exact: bool | None = None
    ) -> Locator:
        """通过占位符文本定位输入框。
        
        Args:
            placeholder: placeholder 属性值
            exact: 是否精确匹配
            
        Returns:
            Locator 对象
            
        Example:
            locator.by_placeholder("请输入用户名")
        """
        kwargs = {}
        if exact is not None:
            kwargs["exact"] = exact
        return self.page.get_by_placeholder(placeholder, **kwargs)
    
    def by_test_id(self, test_id: str) -> Locator:
        """通过 data-testid 属性定位元素。
        
        Args:
            test_id: data-testid 属性值
            
        Returns:
            Locator 对象
            
        Example:
            locator.by_test_id("submit-button")
        """
        return self.page.get_by_test_id(test_id)
    
    def by_alt_text(
        self,
        alt: str,
        exact: bool | None = None
    ) -> Locator:
        """通过 alt 文本定位图片元素。
        
        Args:
            alt: alt 属性值
            exact: 是否精确匹配
            
        Returns:
            Locator 对象
            
        Example:
            locator.by_alt_text("公司logo")
        """
        kwargs = {}
        if exact is not None:
            kwargs["exact"] = exact
        return self.page.get_by_alt_text(alt, **kwargs)
    
    def by_title(
        self,
        title: str,
        exact: bool | None = None
    ) -> Locator:
        """通过 title 属性定位元素。
        
        Args:
            title: title 属性值
            exact: 是否精确匹配
            
        Returns:
            Locator 对象
        """
        kwargs = {}
        if exact is not None:
            kwargs["exact"] = exact
        return self.page.get_by_title(title, **kwargs)
