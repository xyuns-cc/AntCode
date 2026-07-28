"""认证模块

P1-09: refresh token 从纯 JWT 升级为 JWT + 服务端 jti (UserSession 表), 支持
撤销 (改密/离职/泄漏). 相关变更:

- ``create_refresh_token`` 现在返回 ``(token, jti, expires_at)`` 三元组;
  调用方 (登录/刷新路由) 必须解包并异步写 ``UserSession`` 记录。
- ``verify_refresh_token`` 改为 **async**, 校验 payload 的 jti 是否存在于
  UserSession 且未 revoked。
- **兼容性中断**: 老 refresh token (无 jti) 一律 401, 所有用户强制重新登录。
  这是安全升级的合理代价; 前端已有的 401 拦截器会自动跳转登录页。
  若需灰度, 可在下面 verify 分支加临时 warning+accept, 3-7 天后关闭。

调用侧需同步更新: ``services/web_api/.../routes/v1/base.py`` 的 ``login`` 和
``auth/refresh`` 端点。
"""

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from loguru import logger
from pydantic import BaseModel

from antcode_core.common.config import settings

# 统一的认证错误
AUTH_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="无效凭证",
    headers={"WWW-Authenticate": "Bearer"},
)
TOKEN_EXPIRED_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="令牌已过期",
    headers={"WWW-Authenticate": "Bearer"},
)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class JWTSecretManager:
    """JWT 密钥管理器

    密钥必须通过环境变量 ``JWT_SECRET`` 或 ``JWT_SECRET_FILE``
    (亦可通过 ``settings.JWT_SECRET_FILE`` 配置) 显式提供。
    懒生成已禁用以避免多进程 race + 容器重启时密钥丢失。
    """

    def __init__(self, secret_file: Path | str | None = None):
        resolved: Path | None = Path(secret_file) if secret_file else None
        if resolved is None:
            env_file = os.getenv("JWT_SECRET_FILE") or settings.JWT_SECRET_FILE
            if env_file:
                resolved = Path(env_file)
        self.secret_file: Path | None = resolved
        self._secret: str | None = None

    def get_secret(self) -> str:
        if self._secret:
            return self._secret

        # 1. 优先环境变量
        if env_secret := os.getenv("JWT_SECRET"):
            self._secret = env_secret.strip()
            if self._secret:
                return self._validate_secret(self._secret)

        # 2. 从指定文件加载
        if self.secret_file and self.secret_file.exists():
            try:
                if secret := self.secret_file.read_text().strip():
                    self._secret = self._validate_secret(secret)
                    return self._secret
            except Exception:
                logger.exception("读取 JWT 密钥失败")

        # 3. 没配置即拒绝启动 —— 禁止懒生成
        raise RuntimeError(
            "JWT_SECRET or JWT_SECRET_FILE must be configured; "
            "lazy secret generation is disabled to avoid multi-process race "
            "conditions and container-restart key loss."
        )

    @staticmethod
    def _validate_secret(secret: str) -> str:
        if len(secret.encode("utf-8")) < 32:
            raise RuntimeError("JWT_SECRET 必须至少包含 32 字节高熵材料")
        return secret

    def regenerate(self) -> str:
        """重新生成密钥 —— 已禁用,改为运维侧轮换。"""
        raise RuntimeError(
            "JWTSecretManager.regenerate() is disabled; rotate JWT_SECRET via deployment configuration instead."
        )


jwt_secret_manager = JWTSecretManager()


class TokenData(BaseModel):
    """令牌数据"""

    user_id: int
    username: str
    is_admin: bool = False
    role: str = "user"
    exp: datetime
    session_jti: str | None = None

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"


class JWTAuth:
    """JWT 认证处理器"""

    def __init__(self):
        self.algorithm = settings.JWT_ALGORITHM
        self.expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        self.refresh_expire_days = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS

    def _get_secret(self) -> str:
        return jwt_secret_manager.get_secret()

    def _create_token(
        self,
        user_id: int,
        username: str,
        expires_delta: timedelta,
        token_type: str,
        extra: dict | None = None,
    ) -> str:
        expire = _utcnow_naive() + expires_delta
        payload = {
            "user_id": user_id,
            "username": username,
            "exp": expire,
            "token_type": token_type,
        }
        if extra:
            payload.update(extra)
        return jwt.encode(payload, self._get_secret(), algorithm=self.algorithm)

    def create_access_token(
        self,
        user_id: int,
        username: str,
        *,
        expires_delta: timedelta | None = None,
        is_admin: bool = False,
        role: str = "user",
        session_jti: str | None = None,
    ) -> str:
        """创建访问令牌"""
        extra = {
            "is_admin": is_admin,
            "role": role,
            "session_jti": session_jti,
        }
        return self._create_token(
            user_id=user_id,
            username=username,
            expires_delta=expires_delta or timedelta(minutes=self.expire_minutes),
            token_type="access",
            extra=extra,
        )

    def create_refresh_token(
        self, user_id: int, username: str, expires_delta: timedelta | None = None
    ) -> tuple[str, str, datetime]:
        """创建刷新令牌 (P1-09)

        返回 ``(token, jti, expires_at)`` 三元组; 调用方 **必须** 在同一请求
        里 ``await record_refresh_session(user_id, jti, expires_at, ...)`` 把
        jti 写入 UserSession 表, 否则该 refresh token 首次校验就会 401
        ("会话已撤销或不存在")。

        为什么 return 三元组而不是内部直接写 DB:
        - ``create_refresh_token`` 是同步方法, 走内部异步写会引入 loop 依赖
          和 create_task 的失败静默问题;
        - 调用方需要 jti + expires_at 才能在同一 DB 事务里把 session 落库,
          出错时能与 access_token 签发一起回滚。

        Breaking change: 老调用 ``refresh_token = jwt_auth.create_refresh_token(...)``
        会拿到 tuple, 需改为 ``token, jti, exp = jwt_auth.create_refresh_token(...)``
        并异步 ``await record_refresh_session(user_id, jti, exp)``。
        """
        jti = uuid.uuid4().hex  # 32-char, RFC 7519 §4.1.7
        expire = _utcnow_naive() + (expires_delta or timedelta(days=self.refresh_expire_days))
        payload = {
            "user_id": user_id,
            "username": username,
            "sub": str(user_id),
            "exp": expire,
            "token_type": "refresh",
            "jti": jti,
        }
        token = jwt.encode(payload, self._get_secret(), algorithm=self.algorithm)
        return token, jti, expire

    def create_action_token(
        self,
        user_id: int,
        username: str,
        token_type: str,
        expires_delta: timedelta,
    ) -> str:
        """创建一次性操作令牌（如邮箱验证/重置密码）"""
        return self._create_token(
            user_id=user_id,
            username=username,
            expires_delta=expires_delta,
            token_type=token_type,
        )

    def verify_token(
        self,
        token: str,
        expected_type: str | None = "access",
        expected_class: str | None = "web",
    ) -> TokenData:
        """验证令牌

        默认 ``verify_exp=True``、``verify_type=True``、``verify_class=True``。
        调用方若需放宽必须显式传 ``expected_type=None`` / ``expected_class=None``,
        **不允许跳过过期校验**。

        P0-a1: token_class 用于隔离 Web 用户会话(``web``)与 Worker 凭据(``worker``),
        Gateway 侧强制 ``expected_class="worker"``,防止普通 Web access JWT 冒充 Worker。
        为向后兼容,payload 里没有 ``token_class`` 字段时视为 ``"web"``。
        """
        try:
            # 显式强制启用过期校验,杜绝 verify_exp=False 旁路
            payload = jwt.decode(
                token,
                self._get_secret(),
                algorithms=[self.algorithm],
                options={"verify_exp": True, "require": ["exp"]},
            )
            self._verify_token_claims(payload, expected_type, expected_class)
            if payload.get("token_class") == "worker":
                return self._worker_token_data(payload)
            return self._web_token_data(payload)
        except jwt.ExpiredSignatureError:
            raise TOKEN_EXPIRED_ERROR
        except (jwt.InvalidTokenError, jwt.DecodeError):
            raise AUTH_ERROR

    @staticmethod
    def _verify_token_claims(payload, expected_type, expected_class):
        token_type = payload.get("token_type")
        if expected_type and token_type and token_type != expected_type:
            raise AUTH_ERROR
        if expected_type and not token_type and expected_type != "access":
            raise AUTH_ERROR
        actual_class = payload.get("token_class", "web")
        if expected_class and actual_class != expected_class:
            raise AUTH_ERROR

    @staticmethod
    def _worker_token_data(payload):
        worker_id = payload.get("worker_id") or payload.get("sub")
        if not worker_id:
            raise AUTH_ERROR
        return TokenData(
            user_id=0,
            username=str(worker_id),
            is_admin=False,
            role="worker",
            exp=datetime.fromtimestamp(payload["exp"]),
        )

    @staticmethod
    def _web_token_data(payload):
        user_id, username = payload.get("user_id"), payload.get("username")
        if not user_id or not username:
            raise AUTH_ERROR
        is_admin = payload.get("is_admin", False)
        return TokenData(
            user_id=user_id,
            username=username,
            is_admin=is_admin,
            role=payload.get("role", "admin" if is_admin else "user"),
            exp=datetime.fromtimestamp(payload["exp"]),
            session_jti=payload.get("session_jti"),
        )

    def create_worker_token(
        self,
        worker_id: str,
        expires_delta: timedelta | None = None,
    ) -> str:
        """P0-a1: 签发专用于 Worker <-> Gateway 认证的 JWT。

        payload 里 ``token_class="worker"`` + ``worker_id=...``,不携带用户身份信息;
        Gateway ``_authenticate_jwt`` 强制要求 ``expected_class="worker"``,
        拒绝任何 Web access token 冒充 Worker。
        """
        expire = _utcnow_naive() + (expires_delta or timedelta(days=90))
        payload = {
            "sub": worker_id,
            "worker_id": worker_id,
            "token_class": "worker",
            "token_type": "access",
            "exp": expire,
        }
        return jwt.encode(payload, self._get_secret(), algorithm=self.algorithm)


jwt_auth = JWTAuth()
security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


# === 依赖注入函数 ===


async def get_current_user(credentials=Depends(security)) -> TokenData:
    """获取当前用户，并校验 access token 对应的服务端会话。"""
    token_data = jwt_auth.verify_token(credentials.credentials)
    if not token_data.session_jti:
        raise AUTH_ERROR

    from antcode_core.common.security.session_expiry import session_is_unexpired
    from antcode_core.domain.models.user import User
    from antcode_core.domain.models.user_session import UserSession

    now = datetime.now(UTC)
    session = await UserSession.filter(
        jti=token_data.session_jti,
        user_id=token_data.user_id,
        revoked_at__isnull=True,
        expires_at__gt=now,
    ).first()
    if session is None or not session_is_unexpired(session, now=now):
        raise AUTH_ERROR
    user = await User.get_or_none(id=token_data.user_id)
    if user is None or not user.is_active:
        raise AUTH_ERROR

    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return token_data.model_copy(update={"username": user.username, "is_admin": user.is_admin, "role": role})


def get_current_user_id(current_user: TokenData = Depends(get_current_user)) -> int:
    """获取当前用户ID"""
    return current_user.user_id


def get_current_user_from_token(token: str) -> TokenData:
    """从令牌获取用户（同步）"""
    return jwt_auth.verify_token(token)


async def verify_refresh_token(token: str) -> TokenData:
    """验证 refresh JWT 及其服务端 UserSession jti。"""
    # 1) 先做 JWT 结构 / exp / type 校验 (复用现成路径)
    token_data = jwt_auth.verify_token(token, expected_type="refresh")

    # 2) 独立解 payload 拿 jti (verify_token 已完整校验过签名/exp)
    try:
        payload = jwt.decode(
            token,
            jwt_auth._get_secret(),
            algorithms=[jwt_auth.algorithm],
            options={"verify_exp": True, "require": ["exp"]},
        )
    except jwt.PyJWTError:
        # 理论上不可达 (上一步已通过), 保险起见兜底
        raise AUTH_ERROR

    jti = payload.get("jti")
    if not jti:
        # 老 refresh token 无 jti → 强制失效, 触发用户重新登录
        logger.info("P1-09: refresh token 缺少 jti, 拒绝 (老 token 已强制失效)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token 已失效, 请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3) 查服务端 session 状态
    # 延迟 import 避免 auth.py <-> models 循环依赖 (auth 会被 base model
    # 的路径侧引进来)
    from antcode_core.common.security.session_expiry import session_is_unexpired
    from antcode_core.domain.models.user_session import UserSession

    now = datetime.now(UTC)
    session = await UserSession.filter(
        jti=jti,
        user_id=token_data.user_id,
        revoked_at__isnull=True,
        expires_at__gt=now,
    ).first()
    if session is None or not session_is_unexpired(session, now=now):
        logger.info(
            f"P1-09: refresh 拒绝 user={token_data.user_id} jti={jti[:8]}… "
            f"(session={'missing' if session is None else 'expired'})"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="会话已撤销或不存在, 请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token_data.model_copy(update={"session_jti": jti})


async def record_refresh_session(
    user_id: int,
    jti: str,
    expires_at: datetime,
    device_info: str | None = None,
) -> None:
    """P1-09: 把新签发的 refresh token jti 落到 UserSession 表。

    ``create_refresh_token`` 返回 (token, jti, expires_at) 后, 调用方必须
    ``await`` 本函数; 否则该 refresh token 首次 verify 就会 401。

    ``device_info`` 建议传 ``f"{ua_short}|{ip}"``, 便于后续做 "查看登录设备
    / 撤销单个设备" 交互。为空也不影响功能, 只是审计信息缺失。
    """
    from antcode_core.domain.models.user_session import UserSession

    await UserSession.create(
        user_id=user_id,
        jti=jti,
        expires_at=expires_at,
        device_info=device_info,
    )


async def rotate_refresh_session(
    *,
    user_id: int,
    previous_jti: str,
    new_jti: str,
    expires_at: datetime,
    device_info: str | None = None,
) -> None:
    """原子消费旧 refresh session 并创建下一代 session。"""
    from tortoise.transactions import in_transaction

    from antcode_core.domain.models.user_session import UserSession

    async with in_transaction() as connection:
        now = datetime.now(UTC)
        revoked = (
            await UserSession.filter(
                user_id=user_id,
                jti=previous_jti,
                revoked_at__isnull=True,
                expires_at__gt=now,
            )
            .using_db(connection)
            .update(revoked_at=now)
        )
        if revoked != 1:
            raise AUTH_ERROR
        await UserSession.create(
            using_db=connection,
            user_id=user_id,
            jti=new_jti,
            expires_at=expires_at,
            device_info=device_info,
        )


async def revoke_refresh_session(user_id: int, jti: str) -> bool:
    """撤销单个用户会话，供显式登出使用。"""
    from antcode_core.domain.models.user_session import UserSession

    updated = await UserSession.filter(
        user_id=user_id,
        jti=jti,
        revoked_at__isnull=True,
    ).update(revoked_at=datetime.now(UTC))
    return updated == 1


async def verify_token(token: str) -> TokenData:
    """验证令牌（异步）"""
    return jwt_auth.verify_token(token)


async def get_current_admin_user(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """获取当前管理员用户"""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user


async def get_current_super_admin(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """获取超级管理员"""
    if not current_user.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要超级管理员权限")
    return current_user


async def verify_super_admin(user: TokenData) -> bool:
    """验证是否为超级管理员"""
    return user.is_super_admin


async def get_optional_current_user(credentials=Depends(optional_security)) -> TokenData | None:
    """匿名时返回 None；有凭据时复用完整 session/用户实时校验。"""
    if not credentials:
        return None
    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None
