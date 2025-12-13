"""基础模式"""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str
