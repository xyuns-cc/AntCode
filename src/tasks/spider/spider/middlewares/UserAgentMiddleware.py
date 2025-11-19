from fake_useragent import UserAgent
from scrapy.downloadermiddlewares.useragent import UserAgentMiddleware as BaseUserAgentMiddleware
from spider.spider.utils.log import logger


class UserAgentMiddleware(BaseUserAgentMiddleware):
    """
    自定义User-Agent中间件
    - 如果请求没有User-Agent，使用fake-useragent生成Chrome浏览器UA
    - 如果请求已有User-Agent，保持不变
    """
    
    def __init__(self, user_agent=''):
        super().__init__(user_agent)
        try:
            self.ua = UserAgent()
            self.fallback_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        except Exception as e:
            logger.warning(f"初始化fake-useragent失败: {e}, 将使用默认UA")
            self.ua = None
            self.fallback_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    
    def process_request(self, request, spider):
        """
        处理每个请求的User-Agent
        """
        # 检查是否已经有User-Agent
        if b'User-Agent' in request.headers:
            # 已经有User-Agent，不做处理
            return
            
        # 没有User-Agent，生成一个Chrome的UA
        try:
            if self.ua:
                # 使用fake-useragent生成Chrome浏览器的UA
                user_agent = self.ua.chrome
            else:
                user_agent = self.fallback_ua
        except Exception as e:
            logger.warning(f"生成User-Agent失败: {e}, 使用默认值")
            user_agent = self.fallback_ua
            
        request.headers['User-Agent'] = user_agent
        logger.debug(f"设置User-Agent: {user_agent[:50]}...")