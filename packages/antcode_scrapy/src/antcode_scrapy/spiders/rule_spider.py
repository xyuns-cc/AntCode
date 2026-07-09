"""通用规则 Spider：数据驱动，不需要用户写代码。

规则来自前端表单，经 ``ProjectRule.to_dispatch_dict()`` 序列化后传入。
对 Scrapy 的适配层只做三件事：

1. ``start_requests`` — 用 rule.target_url 起初始请求，engine=playwright
   时给 meta 挂 ``playwright: True`` 让 scrapy-playwright 接管下载。
2. ``parse`` — 用 rule.extraction_rules 里的每条 (desc, type, expr) 从
   Scrapy Selector 抽取，一页产出一条 dict 交给 pipeline。
3. ``pagination`` — 简单 next_page_rule（CSS/XPath）驱动分页，受
   ``max_pages`` 限。

**契约兼容点**：抽取表达式（css/xpath/regex）语法与旧 spiderkit 完全
一致，因为 spiderkit 的 Selector 本就是仿 Scrapy 的 parsel；前端已保存的
规则零改动。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

import scrapy


def _xpath_string_literal(s: str) -> str:
    """R1-P2-11: 把任意字符串安全编成 XPath 字符串字面量。

    XPath 字符串字面量没有 ``\\"`` 之类的转义语法，含双引号只能用单引号包，
    含单引号只能用双引号包；两者都含则用 ``concat('a', "'", "b")`` 拼。
    """
    if '"' not in s:
        return f'"{s}"'
    if "'" not in s:
        return f"'{s}'"
    parts = s.split('"')
    # 用双引号包各段（不含 "），中间穿 concat 的 "\"" 拼回去
    concat_parts = []
    for i, part in enumerate(parts):
        if i > 0:
            concat_parts.append('\'"\'')  # XPath: '"'
        if part:
            concat_parts.append(f'"{part}"')
    return f"concat({', '.join(concat_parts)})"


class UniversalRuleSpider(scrapy.Spider):
    """接受一个规则字典（``ProjectRule.to_dispatch_dict()`` 的输出）就能跑。"""

    name = "antcode_rule"

    # 关掉 Scrapy 默认的 robots 遵守（AntCode 已在业务层控制）
    custom_settings: dict[str, Any] = {}

    def __init__(
        self,
        rule: dict[str, Any],
        run_id: str = "",
        project_id: str = "",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if not isinstance(rule, dict):
            raise ValueError("rule 必须是 dict")
        if not rule.get("target_url"):
            raise ValueError("rule.target_url 不能为空")
        extraction_rules = rule.get("extraction_rules") or []
        if not isinstance(extraction_rules, list) or not extraction_rules:
            raise ValueError("rule.extraction_rules 必须是非空数组")

        self.rule = rule
        self.run_id = run_id
        self.project_id = project_id
        self.extraction_rules = extraction_rules
        self.pagination_cfg = rule.get("pagination_config") or {}
        # max_pages 优先取 pagination_config.max_pages，兼容顶层 rule.max_pages
        self.max_pages = int(
            self.pagination_cfg.get("max_pages")
            or rule.get("max_pages")
            or 10
        )
        # start_page 优先级：URL/template 里的 {N} > pagination_config.start_page > rule.start_page > 1
        # 例：``target_url = ".../page/{5}/"`` → start_page=5
        numeric_start = self._extract_numeric_placeholder_start(rule)
        self.start_page = (
            numeric_start
            if numeric_start is not None
            else int(self.pagination_cfg.get("start_page") or rule.get("start_page") or 1)
        )
        self._pages_crawled = 0
        # ``method`` 决定分页策略；旧数据兼容：
        # - 没设 method 但设了 next_page_rule → click_element
        # - target_url 含 {page}/{}/{N} 占位符 → url_pattern
        self.pagination_method = self._infer_pagination_method()

    # ------------------------------------------------------------------
    # 请求
    # ------------------------------------------------------------------
    async def start(self):
        """Scrapy 2.13+ 用 ``async def start`` 替代旧的 ``start_requests``。

        本项目锁 Scrapy>=2.11,<3；2.16 里 ``start_requests`` 已被
        ``StartSpiderMiddleware`` 挡住不再驱动初始请求，必须用 ``start``。

        url_pattern / url_param 模式下**在初始请求就展开多页**，避免依赖
        next 链——某些站点分页元素不稳定，直接模板化 URL 更可靠。
        click_element / infinite_scroll 只发第一页，靠 parse 拉下一页。
        """
        method = self.pagination_method
        # R1-P2-18 (审查报告): url_pattern 但模板无占位符时，老实现会
        # yield max_pages 个**完全相同**的 URL。默认 dont_filter=False 时
        # 被 Scrapy 内置 dupefilter 过滤成静默单页（用户以为拿到 max_pages
        # 页却只有一页）；用户设了 dont_filter=true 时更糟，真正重复请求
        # max_pages 次浪费带宽。在启动时校验模板必须含占位符。
        if method == "url_pattern":
            template = str(
                self.pagination_cfg.get("url_template")
                or self.rule.get("target_url")
                or ""
            )
            if not self._PAGE_PLACEHOLDER_RE.search(template):
                raise ValueError(
                    f"pagination_config.method=url_pattern 但 URL "
                    f"{template!r} 里没有占位符（{{}} / {{page}} / {{page_number}} / "
                    f"{{N}}）。请在目标 URL 或 url_template 里加占位符，"
                    f"否则会重复请求同一 URL。"
                )
        if method in ("url_pattern", "url_param"):
            for page in range(self.start_page, self.start_page + self.max_pages):
                url = self._build_page_url(page)
                if not url:
                    continue
                yield self._build_request(url, page_number=page)
        else:
            yield self._build_request(self.rule["target_url"], page_number=self.start_page)

    def _build_request(self, url: str, page_number: int | None = None) -> scrapy.Request:
        engine = str(self.rule.get("engine") or "").lower()
        meta: dict[str, Any] = {"antcode_page_number": page_number or self.start_page}
        need_page_obj = False
        # js_click 分页强制走 Playwright（即便 rule.engine 没设 playwright）
        if self.pagination_method == "js_click":
            engine = "playwright"
        if engine in ("playwright", "render"):
            meta["playwright"] = True
            # javascript_code / infinite_scroll / js_click 都需要拿 page 对象
            if (
                self.rule.get("javascript_code")
                or self.pagination_method in ("infinite_scroll", "js_click")
            ):
                meta["playwright_include_page"] = True
                need_page_obj = True

        # infinite_scroll 场景：让 scrapy-playwright 打开时执行滚动 PageMethods
        if need_page_obj and self.pagination_method == "infinite_scroll":
            page_methods = self._build_scroll_methods()
            if page_methods:
                meta["playwright_page_methods"] = page_methods
        # js_click 场景：初始加载时先等 DOM 稳定，click 逻辑在 parse 里做
        if need_page_obj and self.pagination_method == "js_click":
            try:
                from scrapy_playwright.page import PageMethod
                meta["playwright_page_methods"] = [
                    PageMethod("wait_for_load_state", "networkidle")
                ]
            except ImportError:
                pass

        # cookies: rule.cookies 可以是 dict 或 list-of-dict，Scrapy 都支持
        return scrapy.Request(
            url=url,
            method=str(self.rule.get("request_method") or "GET").upper(),
            headers=self.rule.get("headers") or {},
            cookies=self.rule.get("cookies") or {},
            meta=meta,
            dont_filter=bool(self.rule.get("dont_filter") or False),
            callback=self.parse,
        )

    def _build_scroll_methods(self) -> list:
        """infinite_scroll: 让 Playwright 页面加载后滚动若干次触发 AJAX 加载。"""
        try:
            from scrapy_playwright.page import PageMethod
        except ImportError:
            self.logger.warning("scrapy_playwright 不可用，infinite_scroll 跳过")
            return []
        scroll_count = int(self.pagination_cfg.get("scroll_count") or 5)
        wait_ms = int(self.pagination_cfg.get("scroll_wait_ms") or 800)
        methods = [PageMethod("wait_for_load_state", "networkidle")]
        for _ in range(scroll_count):
            methods.append(
                PageMethod("evaluate", "window.scrollTo(0, document.body.scrollHeight)")
            )
            methods.append(PageMethod("wait_for_timeout", wait_ms))
        return methods

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    async def parse(self, response, **kwargs):
        self._pages_crawled += 1
        # 把已抓页数写进 Scrapy stats，便于 UI/监控看
        try:
            self.crawler.stats.set_value("antcode/pages_crawled", self._pages_crawled)
        except Exception:
            pass

        # R1-P2-21 (审查报告): Playwright page 泄漏兜底。
        # 老实现有两个泄漏点：
        # (a) page.evaluate 抛异常直接进 except，page 未 close；
        # (b) infinite_scroll + 无 javascript_code 组合下 include_page=True
        #     但 parse 全程无 close。
        # PLAYWRIGHT_MAX_CONTEXTS 默认 4，连续 4 次泄漏就耗尽上下文，
        # 后续请求全部阻塞。这里用 try/finally 保证所有非 js_click 分支
        # 都关掉 page；js_click 分支自己有 finally 关。
        page = response.meta.get("playwright_page")
        js_code = self.rule.get("javascript_code")
        # js_click 路径下 page 要保留给循环 click 用，别提前 close
        keep_page_for_click = (
            page is not None and self.pagination_method == "js_click"
        )
        if page is not None and js_code:
            try:
                await page.evaluate(js_code)
                await page.wait_for_load_state("networkidle")
                # 用渲染后 DOM 重建 selector
                html = await page.content()
                response = response.replace(body=html.encode("utf-8"))
            except Exception as exc:
                self.logger.warning(f"playwright evaluate 失败: {exc}")
                # 出错时非 keep 场景马上关 page（原实现会漏关）

        # R1-P2-21: 非 js_click 路径（含 infinite_scroll、evaluate 出错场景）
        # 现在**统一**在这里关 page；不再依赖各分支各自记得关。
        if page is not None and not keep_page_for_click:
            try:
                await page.close()
            except Exception as pc_exc:
                self.logger.debug(f"page.close 失败（可忽略）: {pc_exc}")

        # 单条 item：一页一条，字段名 = extraction_rules[*].desc
        item: dict[str, Any] = {
            "_url": response.url,
            "_page_number": response.meta.get("antcode_page_number") or self._pages_crawled,
        }
        for r in self.extraction_rules:
            desc = r.get("desc") or r.get("field") or "value"
            item[desc] = self._apply_rule(response, r)
        yield item

        # 分页调度：
        # - url_pattern / url_param 在 ``start`` 已展开，无需 follow
        # - click_element 顺着 next_page_rule 抓 href 递归
        # - infinite_scroll 单页搞定（滚动已在 playwright_page_methods 里执行）
        # - js_click 在同一 Playwright page 里循环点击、抽下一页，一次请求
        #   多页解析（yield 多条 item）
        if self.pagination_method == "click_element":
            if self._pages_crawled >= self.max_pages:
                return
            next_url = self._resolve_next_url(response)
            if next_url:
                next_page = (response.meta.get("antcode_page_number") or self.start_page) + 1
                # response.meta 里有些 scrapy 内部字段不能透传，用白名单挑
                inherited = {
                    k: response.meta[k]
                    for k in ("playwright", "playwright_include_page")
                    if k in response.meta
                }
                inherited["antcode_page_number"] = next_page
                yield response.follow(
                    next_url,
                    callback=self.parse,
                    meta=inherited,
                )

        elif self.pagination_method == "js_click" and page is not None:
            sel_type, expr = self._parse_next_selector()
            playwright_selector = self._to_playwright_selector(sel_type, expr)
            wait_ms = int(
                self.pagination_cfg.get("wait_after_click_ms")
                or self.pagination_cfg.get("scroll_wait_ms")
                or 1000
            )
            current_page = response.meta.get("antcode_page_number") or self.start_page
            try:
                while self._pages_crawled < self.max_pages and playwright_selector:
                    try:
                        locator = page.locator(playwright_selector).first
                        if await locator.count() == 0:
                            self.logger.info(
                                f"js_click: next selector {playwright_selector!r} 不存在，停止翻页"
                            )
                            break
                        await locator.click()
                    except Exception as exc:
                        self.logger.info(f"js_click: click 失败/无更多页: {exc}")
                        break
                    try:
                        await page.wait_for_load_state("networkidle", timeout=wait_ms + 5000)
                    except Exception:
                        await page.wait_for_timeout(wait_ms)
                    else:
                        # 保底再等 wait_ms，防止 SPA 局部渲染没触发 networkidle
                        await page.wait_for_timeout(wait_ms)

                    self._pages_crawled += 1
                    current_page += 1
                    html = await page.content()
                    fake_response = response.replace(
                        body=html.encode("utf-8"),
                        url=page.url,
                    )
                    item2: dict[str, Any] = {
                        "_url": page.url,
                        "_page_number": current_page,
                    }
                    for r in self.extraction_rules:
                        desc = r.get("desc") or r.get("field") or "value"
                        item2[desc] = self._apply_rule(fake_response, r)
                    yield item2
                    try:
                        self.crawler.stats.set_value(
                            "antcode/pages_crawled", self._pages_crawled
                        )
                    except Exception:
                        pass
            finally:
                try:
                    await page.close()
                except Exception:
                    pass

    def _apply_rule(self, response, rule: dict[str, Any]) -> Any:
        """一条规则：type=css/xpath/regex；单值返元素，多值返 list。"""
        rule_type = str(rule.get("type") or "css").lower()
        expr = rule.get("expr") or ""
        if not expr:
            return None
        try:
            if rule_type == "css":
                values = response.css(expr).getall()
            elif rule_type == "xpath":
                values = response.xpath(expr).getall()
            elif rule_type in ("regex", "re"):
                # R1-P2-11: 简单 ReDoS 兜底——超长表达式或嵌套量词直接拒绝。
                # 更彻底的方案是用支持超时的正则引擎（如 regex 库或 re2），
                # 一期先做规则层拦截，避免恶意规则拖挂 worker。
                if len(expr) > 512:
                    self.logger.warning(
                        f"regex 规则过长（{len(expr)}> 512）拒绝执行 desc={rule.get('desc')}"
                    )
                    return None
                # 嵌套量词的启发式：(...)+... 或 (...)*... 里面又含 + 或 *
                if re.search(r"\([^)]*[+*][^)]*\)[+*]", expr):
                    self.logger.warning(
                        f"regex 可能存在 catastrophic backtracking 拒绝执行: {expr!r}"
                    )
                    return None
                text = response.text if hasattr(response, "text") else ""
                values = re.findall(expr, text)
            else:
                raise ValueError(f"不支持的规则类型: {rule_type}")
        except Exception as exc:
            self.logger.warning(f"抽取失败 desc={rule.get('desc')} expr={expr!r}: {exc}")
            return None
        if not values:
            return None
        return values if len(values) > 1 else values[0]

    def _resolve_next_url(self, response) -> str | None:
        """click_element 模式下从 HTML 里抓 next 链接的 href。

        接受三种 ``next_page_rule`` 形式（S8）：
        - 字符串（旧兼容）：自动判 CSS / XPath
          - 以 ``//`` / ``..`` / ``xpath=`` 开头 → XPath
          - 其他 → CSS，若不含 ``::attr`` 自动补 ``::attr(href)``
        - 对象：``{"type": "css|xpath", "expr": "..."}``
        - HTTP 引擎下 ``type=text`` 无法定位 href，退化为 CSS 全文找
          最近的 ``<a>``——**推荐用户在 text 场景切换 js_click**。
        """
        sel_type, expr = self._parse_next_selector()
        if not expr:
            return None
        try:
            if sel_type == "xpath":
                # 保证抓到 href：如果表达式没显式取 @href 而是选到 <a>，补一层
                target = expr if "@href" in expr or expr.endswith("/text()") else f"({expr})/@href"
                href = response.xpath(target).get()
                if not href:
                    href = response.xpath(expr).get()
            elif sel_type == "text":
                # HTTP 模式下没有原生 text engine，用 XPath 拼一下
                # 例：expr="Next" → //a[contains(normalize-space(.), "Next")]/@href
                # R1-P2-11 (审查报告): 老实现 ``expr.replace('"','\\"')`` 是
                # 错的——XPath 字符串字面量不支持 \\" 转义，含双引号的 expr
                # 直接抛 ValueError 被 except 吞掉，翻页静默终止。正确做法：
                # 若含双引号则用单引号包，两者都含则用 XPath concat() 拼。
                target = _xpath_string_literal(expr)
                target = f'//a[contains(normalize-space(.), {target})]/@href'
                href = response.xpath(target).get()
            else:
                # CSS：自动补 ::attr(href) 让抓 <a> 也能拿到链接
                with_href = expr if "::attr" in expr else f"{expr}::attr(href)"
                href = response.css(with_href).get()
                if not href:
                    href = response.css(expr).get()
        except Exception:
            return None
        return href or None

    def _parse_next_selector(self) -> tuple[str, str]:
        """归一化 ``pagination_config.next_page_rule`` → ``(type, expr)``。

        允许：
        - 字符串（老兼容）：启发式识别 CSS / XPath
        - 对象：``{"type": "css|xpath|text", "expr": "..."}``
        """
        raw = self.pagination_cfg.get("next_page_rule")
        if isinstance(raw, dict):
            sel_type = str(raw.get("type") or "css").lower()
            expr = str(raw.get("expr") or "").strip()
            if sel_type not in ("css", "xpath", "text"):
                sel_type = "css"
            return sel_type, expr
        text = str(raw or "").strip()
        if not text:
            return "css", ""
        if text.startswith(("//", "..", "xpath=")):
            return "xpath", text.removeprefix("xpath=")
        return "css", text

    # ------------------------------------------------------------------
    # 分页方法推断 + URL 构造（S6/S7）
    # ------------------------------------------------------------------
    # S7: 除了 {page}/{page_number}/{} 三种"纯占位符"（起始页由 start_page
    # 或缺省 1 决定），还额外支持 {N} 数字占位符——N 直接就是起始页号，
    # 更直观，允许 `page{1}` `page/{5}/` 等任意前后缀（无需路径分隔符）。
    _NAMED_PLACEHOLDER_RE = re.compile(r"\{page(?:_number)?\}|\{\}", re.IGNORECASE)
    _NUMERIC_PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")
    _PAGE_PLACEHOLDER_RE = re.compile(r"\{page(?:_number)?\}|\{\}|\{\d+\}", re.IGNORECASE)

    def _infer_pagination_method(self) -> str:
        """从 pagination_config.method 显式读取；缺失时按启发式推断。

        支持的 method：
        - ``none``           单页
        - ``url_pattern``    URL 里带占位符（``{}`` / ``{page}`` / ``{N}``），
                              在初始请求就展开多页
        - ``url_param``      在 target_url 上追加 ``?page=N`` query
        - ``click_element``  抓 ``next_page_rule`` 的 href 递归 follow
        - ``js_click``       Playwright 引擎下**点击**按钮翻页（HTML 里
                              不是 <a href> 而是 <button> / JS 拦截 <a>）
        - ``infinite_scroll``Playwright 引擎下滚动加载

        兼容旧枚举值 ``javascript`` / ``ajax`` → 归一到 ``infinite_scroll``
        （历史上都是"JS 触发增量加载"语义）。
        """
        explicit = str(self.pagination_cfg.get("method") or "").strip().lower()
        allowed = {
            "none",
            "url_pattern",
            "click_element",
            "url_param",
            "infinite_scroll",
            "js_click",
            "javascript",
            "ajax",
        }
        if explicit in allowed:
            if explicit in ("javascript", "ajax"):
                return "infinite_scroll"
            return explicit
        # 启发式
        template = str(self.pagination_cfg.get("url_template") or "").strip()
        target = str(self.rule.get("target_url") or "")
        if template and self._PAGE_PLACEHOLDER_RE.search(template):
            return "url_pattern"
        if self._PAGE_PLACEHOLDER_RE.search(target):
            return "url_pattern"
        if self.pagination_cfg.get("page_param"):
            return "url_param"
        if str(self.pagination_cfg.get("next_page_rule") or "").strip():
            return "click_element"
        return "none"

    def _build_page_url(self, page: int) -> str | None:
        """按 method 生成第 N 页 URL。

        占位符处理（url_pattern）：
        - ``{}``          → 直接替换第一次出现（保留 str.format 风格）
        - ``{page}`` / ``{page_number}`` → 命名占位符
        - ``{N}``         → 数字占位符（N 是初始页号，抓取时按 page 递增替换）

        对于 ``{N}``，无论前后有无路径分隔符都能命中——``page/{1}/`` 变
        ``page/1/`` `page/2/` ...；``page{1}`` 变 ``page1`` `page2` ...；
        ``list?p={0}`` 变 ``list?p=0`` `list?p=1` ... （0-indexed 也 OK）。
        """
        if self.pagination_method == "url_pattern":
            template = str(
                self.pagination_cfg.get("url_template")
                or self.rule.get("target_url")
                or ""
            )
            if not template:
                return None
            # {} 语义：str.format 风格，只替换第一次
            if "{}" in template:
                return template.replace("{}", str(page), 1)
            # 优先命中数字 {N}（S7：N 起始页由构造函数已提取过，这里所有
            # {N} 都统一替换成当前 page，不管原本 N 是几）
            def _replace_num(m: "re.Match[str]") -> str:
                return str(page)
            replaced = self._NUMERIC_PLACEHOLDER_RE.sub(_replace_num, template)
            # 再替换命名 {page}/{page_number}
            return self._NAMED_PLACEHOLDER_RE.sub(str(page), replaced)
        if self.pagination_method == "url_param":
            base = str(self.rule.get("target_url") or "")
            param = str(self.pagination_cfg.get("page_param") or "page")
            return self._set_query_param(base, param, page)
        return None

    def _extract_numeric_placeholder_start(self, rule: dict[str, Any]) -> int | None:
        """从 target_url / url_template 里抽出第一个 ``{N}`` 的 N 作为起始页。

        没有 ``{N}`` 时返回 None，走 config.start_page 或缺省 1。
        """
        candidates = [
            str(self.pagination_cfg.get("url_template") or ""),
            str(rule.get("target_url") or ""),
        ]
        for text in candidates:
            m = self._NUMERIC_PLACEHOLDER_RE.search(text)
            if m:
                return int(m.group(1))
        return None

    @staticmethod
    def _to_playwright_selector(sel_type: str, expr: str) -> str:
        """把 (type, expr) 编成 Playwright locator 字符串。

        Playwright locator 自动识别 ``//`` 开头为 XPath，但为了避免歧义
        （比如 XPath 是 ``count(//foo)`` 之类非 // 开头），加显式前缀。
        """
        if not expr:
            return ""
        st = sel_type.lower()
        if st == "xpath":
            return expr if expr.startswith(("//", "..", "xpath=")) else f"xpath={expr}"
        if st == "text":
            return expr if expr.startswith("text=") else f"text={expr}"
        # css: Playwright 默认就是 CSS
        return expr

    @staticmethod
    def _set_query_param(url: str, key: str, value: Any) -> str:
        """把 ?key=value 加到/替换 URL 里，保留其他 query。"""
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query[key] = [str(value)]
        new_query = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
