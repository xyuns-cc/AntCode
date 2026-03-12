"""Worker 项目下载 URL 工具。"""

from __future__ import annotations

import time
import uuid
from urllib.parse import urlencode

from antcode_core.common.config import settings
from antcode_core.common.security import generate_hmac_signature


def build_worker_download_url(worker, project_public_id: str) -> str:
    if not getattr(worker, "public_id", None):
        raise ValueError("Worker 标识缺失")
    if not getattr(worker, "secret_key", None):
        raise ValueError("Worker secret_key 缺失")

    timestamp = int(time.time())
    nonce = uuid.uuid4().hex[:16]
    payload = {"project_id": project_public_id}
    headers = generate_hmac_signature(payload, worker.secret_key, timestamp, nonce)
    params = urlencode(
        {
            "worker_id": worker.public_id,
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": headers["X-Signature"],
        }
    )
    return f"{get_api_base_url()}/api/v1/projects/{project_public_id}/worker-download?{params}"


def get_api_base_url() -> str:
    base_url = (settings.API_BASE_URL or "").strip()
    if base_url:
        return base_url.rstrip("/")
    return f"http://{settings.SERVER_DOMAIN}:{settings.SERVER_PORT}"

