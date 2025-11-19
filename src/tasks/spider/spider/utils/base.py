
from copy import deepcopy


class ScheduledRequest:
    """
    封装Redis中的请求数据
    """

    _KNOWN_FIELDS = {
        'url',
        'method',
        'callback',
        'body',
        'data',
        'headers',
        'cookies',
        'meta',
        'priority',
        'dont_filter',
        'proxy',
    }

    def __init__(self, **kwargs):
        self.url = kwargs.get('url')
        self.method = kwargs.get('method', 'GET')
        self.callback = kwargs.get('callback')

        self.body = kwargs.get('body')
        self.data = kwargs.get('data')
        if self.body is None and self.data is not None:
            self.body = self.data

        self.headers = deepcopy(kwargs.get('headers') or {})
        self.cookies = deepcopy(kwargs.get('cookies') or {})
        self.meta = deepcopy(kwargs.get('meta') or {})
        self.priority = kwargs.get('priority', 0)
        self.dont_filter = kwargs.get('dont_filter', False)
        self.proxy = kwargs.get('proxy')

        self.extra = {
            key: deepcopy(value)
            for key, value in kwargs.items()
            if key not in self._KNOWN_FIELDS
        }

    def to_dict(self):
        payload = {
            'url': self.url,
            'method': self.method,
            'callback': self.callback,
            'headers': deepcopy(self.headers),
            'cookies': deepcopy(self.cookies),
            'meta': deepcopy(self.meta),
            'priority': self.priority,
            'dont_filter': self.dont_filter,
        }

        if self.body is not None:
            payload['body'] = self.body
        if self.data is not None:
            payload['data'] = self.data
        if self.proxy is not None:
            payload['proxy'] = self.proxy
        if self.extra:
            for key, value in self.extra.items():
                payload[key] = deepcopy(value)

        return payload
