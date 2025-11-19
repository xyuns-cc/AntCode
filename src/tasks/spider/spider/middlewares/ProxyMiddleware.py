import base64
from urllib.parse import urlparse
from scrapy.exceptions import NotConfigured
from spider.spider.utils.log import logger


class ProxyMiddleware:
    """
    动态代理中间件，根据request.meta中的proxy字段自动设置代理
    支持HTTP、HTTPS和SOCKS(4/5)代理
    
    支持的代理格式：
    - http://proxy.example.com:8080
    - http://user:password@proxy.example.com:8080
    - socks5://user:password@proxy.example.com:1080
    - socks4://proxy.example.com:1080
    """
    
    def __init__(self):
        self.logger = logger
    
    @classmethod
    def from_crawler(cls, crawler):
        s = cls()
        s.logger = crawler.spider.logger
        return s
    
    def process_request(self, request, spider):
        """
        处理每个请求，从meta中提取代理信息并设置
        """
        proxy = request.meta.get('proxy')
        if not proxy:
            return
            
        # 解析代理URL
        parsed = urlparse(proxy)
        
        # 设置代理
        request.meta['proxy'] = proxy
        
        # 处理代理认证
        if parsed.username and parsed.password:
            # 对于HTTP代理，需要设置Proxy-Authorization header
            if parsed.scheme in ('http', 'https'):
                # 构建认证字符串
                auth = f"{parsed.username}:{parsed.password}"
                encoded_auth = base64.b64encode(auth.encode()).decode('ascii')
                request.headers['Proxy-Authorization'] = f'Basic {encoded_auth}'
                
                # 重构代理URL（去掉用户名密码）
                proxy_without_auth = f"{parsed.scheme}://{parsed.hostname}"
                if parsed.port:
                    proxy_without_auth += f":{parsed.port}"
                request.meta['proxy'] = proxy_without_auth
                
                self.logger.debug(f"使用代理: {proxy_without_auth} (with auth)")
            else:
                # SOCKS代理保持原样
                self.logger.debug(f"使用SOCKS代理: {proxy}")
        else:
            self.logger.debug(f"使用代理: {proxy}")
            
    def process_response(self, request, response, spider):
        """
        可选：记录代理使用情况
        """
        if 'proxy' in request.meta:
            self.logger.debug(f"通过代理 {request.meta['proxy']} 成功获取: {response.url}")
        return response
        
    def process_exception(self, request, exception, spider):
        """
        处理代理相关的异常
        """
        if 'proxy' in request.meta:
            self.logger.error(f"代理 {request.meta['proxy']} 请求失败: {request.url}, 错误: {exception}")
        # 返回None让其他中间件处理
        return None