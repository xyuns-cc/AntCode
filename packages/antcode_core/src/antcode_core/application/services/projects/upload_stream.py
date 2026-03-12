"""内存文件流包装。"""

from __future__ import annotations

from io import BytesIO


class InMemoryUploadFile:
    """适配文件存储后端的最小上传对象。"""

    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.size = len(content)
        self._buffer = BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    async def seek(self, offset: int) -> int:
        return self._buffer.seek(offset)
