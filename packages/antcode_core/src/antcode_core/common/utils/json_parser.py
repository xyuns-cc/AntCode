"""严格 JSON 解析工具模块。"""

import ujson


class JSONParser:
    """JSON解析器类"""

    @staticmethod
    def parse_safely(data, field_name="data"):
        """
        安全解析JSON字符串或字典

        Args:
            data: 待解析的数据，可以是字符串、字典或None
            field_name: 字段名称，用于日志记录

        Returns:
            解析后的字典对象，失败时返回None
        """
        if data is None:
            return None

        # 如果已经是字典类型，直接返回
        if isinstance(data, dict):
            return data

        if not isinstance(data, str):
            raise ValueError(f"{field_name} 必须是 JSON 对象字符串或字典")

        # 去除首尾空白字符
        data = data.strip()
        if not data:
            return None

        try:
            result = ujson.loads(data)
        except ValueError as exc:
            raise ValueError(f"{field_name} JSON格式错误") from exc
        if not isinstance(result, dict):
            raise ValueError(f"{field_name} 必须是 JSON 对象")
        return result

    @staticmethod
    def parse_list(data, field_name="data"):
        """
        安全解析JSON数组

        Args:
            data: 待解析的数据，可以是字符串、列表或None
            field_name: 字段名称，用于日志记录

        Returns:
            解析后的列表对象，失败时返回None
        """
        if data is None:
            return None

        # 如果已经是列表类型，直接返回
        if isinstance(data, list):
            return data

        if not isinstance(data, str):
            raise ValueError(f"{field_name} 必须是 JSON 数组字符串或列表")

        # 去除首尾空白字符
        data = data.strip()
        if not data:
            return None

        try:
            result = ujson.loads(data)
        except ValueError as exc:
            raise ValueError(f"{field_name} JSON格式错误") from exc
        if not isinstance(result, list):
            raise ValueError(f"{field_name} 必须是 JSON 数组")
        return result

    @staticmethod
    def parse_extraction_rules(data, field_name="extraction_rules"):
        """
        解析提取规则JSON字符串

        Args:
            data: 待解析的数据，可以是字符串、列表或None
            field_name: 字段名称，用于日志记录

        Returns:
            解析后的提取规则列表，失败时返回None
        """
        if data is None:
            return None

        # 如果已经是列表类型，直接返回
        if isinstance(data, list):
            return data

        # 使用 parse_list 解析
        result = JSONParser.parse_list(data, field_name)
        if result is None:
            return None

        # 验证每个元素都是字典
        for i, item in enumerate(result):
            if not isinstance(item, dict):
                raise ValueError(f"{field_name}[{i}] 必须是 JSON 对象")

        return result

    @staticmethod
    def parse_pagination_config(data, field_name="pagination_config"):
        """
        解析分页配置JSON字符串

        Args:
            data: 待解析的数据，可以是字符串、字典或None
            field_name: 字段名称，用于日志记录

        Returns:
            解析后的分页配置字典，失败时返回None
        """
        return JSONParser.parse_safely(data, field_name)

    @staticmethod
    def parse_headers(headers):
        """
        解析请求头数据

        Args:
            headers: 请求头数据

        Returns:
            解析后的请求头字典
        """
        result = JSONParser.parse_safely(headers, "headers")
        if result is None:
            return None

        # 确保所有值都是字符串类型
        return {str(k): str(v) for k, v in result.items()}

    @staticmethod
    def parse_cookies(cookies):
        """
        解析Cookie数据

        Args:
            cookies: Cookie数据

        Returns:
            解析后的Cookie字典
        """
        result = JSONParser.parse_safely(cookies, "cookies")
        if result is None:
            return None

        # 确保所有值都是字符串类型
        return {str(k): str(v) for k, v in result.items()}

    @staticmethod
    def parse_config(config, config_name="config"):
        """
        解析通用配置数据

        Args:
            config: 配置数据
            config_name: 配置名称，用于日志记录

        Returns:
            解析后的配置字典
        """
        return JSONParser.parse_safely(config, config_name)


# 提供便捷的模块级函数
def parse_json_safely(data, field_name="data"):
    """便捷的JSON解析函数"""
    return JSONParser.parse_safely(data, field_name)


def parse_headers(headers):
    """便捷的请求头解析函数"""
    return JSONParser.parse_headers(headers)


def parse_cookies(cookies):
    """便捷的Cookie解析函数"""
    return JSONParser.parse_cookies(cookies)
