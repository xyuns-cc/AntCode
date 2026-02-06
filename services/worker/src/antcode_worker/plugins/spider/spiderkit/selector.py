"""
选择器 - XPath/CSS/正则解析

基于 lxml，高性能 HTML/XML 解析
"""

import re
from typing import Any

try:
    from lxml import etree
    from lxml.html import HtmlElement
    from lxml.html import fromstring as html_fromstring

    HAS_LXML = True
except ImportError:
    HAS_LXML = False
    etree = None
    HtmlElement = None

try:
    from cssselect import GenericTranslator

    HAS_CSSSELECT = True
except ImportError:
    HAS_CSSSELECT = False
    GenericTranslator = None


class SelectorList(list):
    """选择器结果列表"""

    def xpath(self, query: str) -> "SelectorList":
        """链式 XPath"""
        result = SelectorList()
        for sel in self:
            result.extend(sel.xpath(query))
        return result

    def css(self, query: str) -> "SelectorList":
        """链式 CSS"""
        result = SelectorList()
        for sel in self:
            result.extend(sel.css(query))
        return result

    def re(self, pattern: str, flags: int = 0) -> list[str]:
        """正则匹配所有"""
        result = []
        for sel in self:
            result.extend(sel.re(pattern, flags))
        return result

    def re_first(
        self, pattern: str, default: str = None, flags: int = 0
    ) -> str | None:
        """正则匹配第一个"""
        for sel in self:
            match = sel.re_first(pattern, flags=flags)
            if match is not None:
                return match
        return default

    def get(self, default: str = None) -> str | None:
        """获取第一个文本"""
        return self[0].get() if self else default

    def getall(self) -> list[str]:
        """获取所有文本"""
        return [sel.get() for sel in self]

    def attrib(self, name: str, default: str = None) -> str | None:
        """获取第一个属性"""
        return self[0].attrib(name, default) if self else default


class Selector:
    """
    选择器

    支持 XPath、CSS、正则表达式

    用法:
        sel = Selector(html_text)

        # XPath
        titles = sel.xpath("//h1/text()").getall()

        # CSS
        links = sel.css("a::attr(href)").getall()

        # 正则
        prices = sel.re(r"\\$([0-9]+\\.?[0-9]*)")
    """

    def __init__(self, text: str = None, root: Any = None, type: str = "html"):
        """
        Args:
            text: HTML/XML 文本
            root: lxml 元素
            type: 解析类型 (html/xml)
        """
        if not HAS_LXML:
            raise ImportError("需要安装 lxml: pip install lxml")

        self._text = text
        self._type = type

        if root is not None:
            self._root = root
        elif text:
            if type == "html":
                self._root = html_fromstring(text)
            else:
                self._root = etree.fromstring(text.encode())
        else:
            self._root = None

    def xpath(self, query: str) -> SelectorList:
        """
        XPath 选择

        Args:
            query: XPath 表达式

        Returns:
            SelectorList
        """
        if self._root is None:
            return SelectorList()

        try:
            result = self._root.xpath(query)
        except etree.XPathError as e:
            raise ValueError(f"XPath 错误: {e}")

        if isinstance(result, list):
            return SelectorList(
                [
                    (
                        Selector(root=item, type=self._type)
                        if isinstance(item, HtmlElement) or hasattr(item, "xpath")
                        else _TextSelector(str(item))
                    )
                    for item in result
                ]
            )
        else:
            return SelectorList([_TextSelector(str(result))])

    def css(self, query: str) -> SelectorList:
        """
        CSS 选择

        支持伪元素:
        - ::text - 文本内容
        - ::attr(name) - 属性值

        Args:
            query: CSS 选择器

        Returns:
            SelectorList
        """
        if not HAS_CSSSELECT:
            raise ImportError("需要安装 cssselect: pip install cssselect")

        # 处理伪元素
        pseudo_text = False
        pseudo_attr = None

        if "::text" in query:
            query = query.replace("::text", "")
            pseudo_text = True
        elif "::attr(" in query:
            import re

            match = re.search(r"::attr\(([^)]+)\)", query)
            if match:
                pseudo_attr = match.group(1)
                query = re.sub(r"::attr\([^)]+\)", "", query)

        # CSS 转 XPath
        translator = GenericTranslator()
        xpath_query = translator.css_to_xpath(query.strip())

        # 添加伪元素处理
        if pseudo_text:
            xpath_query += "/text()"
        elif pseudo_attr:
            xpath_query += f"/@{pseudo_attr}"

        return self.xpath(xpath_query)

    def re(self, pattern: str, flags: int = 0) -> list[str]:
        """
        正则匹配

        Args:
            pattern: 正则表达式
            flags: 正则标志

        Returns:
            匹配结果列表
        """
        text = self.get() or ""
        compiled = re.compile(pattern, flags)
        matches = compiled.findall(text)

        # 处理分组
        result = []
        for match in matches:
            if isinstance(match, tuple):
                result.extend(match)
            else:
                result.append(match)
        return result

    def re_first(
        self, pattern: str, default: str = None, flags: int = 0
    ) -> str | None:
        """正则匹配第一个"""
        matches = self.re(pattern, flags)
        return matches[0] if matches else default

    def get(self, default: str = None) -> str | None:
        """获取文本内容"""
        if self._root is None:
            return default

        if isinstance(self._root, str):
            return self._root

        if hasattr(self._root, "text_content"):
            return self._root.text_content()

        return etree.tostring(self._root, encoding="unicode", method="text")

    def getall(self) -> list[str]:
        """获取所有文本"""
        text = self.get()
        return [text] if text else []

    def attrib(self, name: str, default: str = None) -> str | None:
        """获取属性"""
        if self._root is None or not hasattr(self._root, "attrib"):
            return default
        return self._root.attrib.get(name, default)

    def extract(self) -> str | None:
        """提取 HTML"""
        if self._root is None:
            return None
        if isinstance(self._root, str):
            return self._root
        return etree.tostring(self._root, encoding="unicode")

    def extract_first(self, default: str = None) -> str | None:
        """提取第一个 HTML"""
        return self.extract() or default


class _TextSelector(Selector):
    """文本选择器（用于 XPath 文本结果）"""

    def __init__(self, text: str):
        self._text = text
        self._root = text
        self._type = "text"

    def get(self, default: str = None) -> str | None:
        return self._text if self._text else default

    def xpath(self, query: str) -> SelectorList:
        return SelectorList()

    def css(self, query: str) -> SelectorList:
        return SelectorList()
