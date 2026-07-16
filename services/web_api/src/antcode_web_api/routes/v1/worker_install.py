"""Worker 安装脚本下载端点。"""

from fastapi import APIRouter, Response

from antcode_web_api.services.worker_installer import install_script_sha256, read_install_script

router = APIRouter()


def _script_response(script_name: str, media_type: str) -> Response:
    digest = install_script_sha256(script_name)
    return Response(
        content=read_install_script(script_name),
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=300",
            "ETag": f'"{digest}"',
            "X-Content-SHA256": digest,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/install.sh", summary="下载 Linux/macOS Worker 安装脚本")
def download_unix_worker_installer() -> Response:
    return _script_response("install_worker.sh", "text/x-shellscript")


@router.get("/install.ps1", summary="下载 Windows Worker 安装脚本")
def download_windows_worker_installer() -> Response:
    return _script_response("install_worker.ps1", "text/plain")


__all__ = ["router"]
