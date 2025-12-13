# logger/alert_channels/feishu.py
from src.services.alert.alert_channels.base import MultiWebhookChannel
from datetime import datetime
import re


class FeishuAlertChannel(MultiWebhookChannel):
    """飞书告警渠道"""

    def _parse_log_message(self, message):
        """解析日志消息"""
        parts = message.split(' | ')

        if len(parts) >= 4:
            timestamp = parts[0].strip()
            log_level = parts[1].strip()
            location = parts[2].strip()
            log_message = ' | '.join(parts[3:]).strip()
        else:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            log_level = 'UNKNOWN'
            location = '未知位置'
            log_message = message

        has_traceback = False
        full_traceback = None
        exception_type = None
        exception_msg = None

        if 'Traceback (most recent call last):' in log_message:
            has_traceback = True

            if '\nTraceback' in log_message:
                parts = log_message.split('\nTraceback', 1)
                exception_msg = parts[0].strip()
                full_traceback = 'Traceback' + parts[1]
            else:
                full_traceback = log_message
                exception_msg = "发生异常"

            lines = full_traceback.strip().split('\n')
            for line in reversed(lines):
                line = line.strip()
                if line and ':' in line and not line.startswith('File'):
                    match = re.match(r'^(\w+(?:\.\w+)*(?:Error|Exception|Warning)?)\s*:\s*(.*)$', line)
                    if match:
                        exception_type = match.group(1)
                        break

        return {
            'timestamp': timestamp,
            'log_level': log_level,
            'location': location,
            'message': exception_msg if has_traceback else log_message,
            'has_traceback': has_traceback,
            'traceback': full_traceback,
            'exception_type': exception_type,
            'exception_msg': exception_msg
        }

    def _build_payload(self, message, level):
        """构建飞书消息载荷"""
        level_config = {
            'TRACE': {'icon': '[TRACE]', 'color': 'grey', 'title': 'TRACE 跟踪'},
            'DEBUG': {'icon': '[DEBUG]', 'color': 'blue', 'title': 'DEBUG 调试'},
            'INFO': {'icon': '[INFO]', 'color': 'blue', 'title': 'INFO 信息'},
            'SUCCESS': {'icon': '[SUCCESS]', 'color': 'green', 'title': 'SUCCESS 成功'},
            'WARNING': {'icon': '[WARNING]', 'color': 'orange', 'title': 'WARNING 警告'},
            'ERROR': {'icon': '[ERROR]', 'color': 'red', 'title': 'ERROR 错误'},
            'CRITICAL': {'icon': '[CRITICAL]', 'color': 'red', 'title': 'CRITICAL 严重错误'}
        }

        config = level_config.get(level, {'icon': f'[{level}]', 'color': 'grey', 'title': level})
        parsed = self._parse_log_message(message)

        elements = []

        # 基础信息
        elements.append({
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**时间**\n{parsed['timestamp']}"
                    }
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**位置**\n`{parsed['location']}`"
                    }
                }
            ]
        })

        # 消息内容
        if parsed['has_traceback'] and parsed['exception_msg']:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**异常信息**\n{parsed['exception_msg']}"
                }
            })

            if parsed['exception_type']:
                elements.append({
                    "tag": "div",
                    "fields": [
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**异常类型**\n`{parsed['exception_type']}`"
                            }
                        }
                    ]
                })
        else:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**消息内容**\n{parsed['message']}"
                }
            })

        # 堆栈跟踪
        if parsed['has_traceback'] and parsed['traceback']:
            stack_lines = parsed['traceback'].split('\n')
            line_count = len(stack_lines)

            # 堆栈标题
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**异常堆栈** ({line_count} 行)"
                }
            })

            # 堆栈内容
            elements.append({
                "tag": "markdown",
                "content": f"```\n{parsed['traceback']}\n```"
            })

        # 页脚
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"级别: {parsed['log_level']} | 来源: Spider 自动化平台"
                }
            ]
        })

        return {
            "msg_type": "interactive",
            "card": {
                "config": {
                    "wide_screen_mode": True
                },
                "header": {
                    "template": config['color'],
                    "title": {
                        "tag": "plain_text",
                        "content": f"{config['icon']} {config['title']}"
                    }
                },
                "elements": elements
            }
        }

    def _check_response(self, data):
        """检查飞书响应"""
        try:
            if data.get("code") == 0 or data.get("StatusCode") == 0:
                return True, ""
            return False, data.get("msg", str(data))
        except (KeyError, TypeError, AttributeError):
            return False, "响应解析失败"

    @property
    def channel_name(self):
        return "feishu"

