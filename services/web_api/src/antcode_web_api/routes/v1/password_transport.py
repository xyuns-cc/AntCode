"""口令的传输形态：把「明文或密文」二选一的请求字段收敛成明文。

登录、用户改密、管理员重置，以及**管理员建号时设的初始口令**，提交的都是同
一类机密。前三条早已共用 ``resolve_transmitted_password``，建号这条却一直只
收明文——"口令不得明文过线"因此只做了四分之三。策略入口必须只有一个，所以
这里既不另起一套开关，也不另起一套状态码。

策略执行在**路由层**，不在 schema 或 service 层，因为它管的是"口令不得明文
过线"——只对真正过线的请求成立。引导默认管理员的 ``scripts/init_db.py::_create_admin``
拿本进程环境变量里的 ``DEFAULT_ADMIN_PASSWORD`` 直接落 ``User`` 模型，既不过网络也
拿不到公钥；策略留在路由层，这条路径才不会被"过线"的规矩误伤。
"""

from antcode_core.common.security.login_crypto import (
    LoginPasswordCryptoError,
    resolve_transmitted_password,
)
from antcode_core.domain.schemas.user import PasswordEnvelopeFields, UserCreateRequest
from fastapi import HTTPException, status


def decrypt_transmitted(encrypted: str | None, plaintext: str | None, envelope: PasswordEnvelopeFields) -> str:
    """把一个口令字段收敛成明文；密文格式/策略错误一律 400。

    与登录同一个异常类型和同一个状态码，避免两个端点对同一种错误给出两套说辞。
    """
    try:
        return resolve_transmitted_password(
            plaintext=plaintext,
            encrypted=encrypted,
            algorithm=envelope.encryption,
            key_id=envelope.key_id,
        )
    except LoginPasswordCryptoError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def resolve_created_user(request: UserCreateRequest) -> UserCreateRequest:
    """返回初始口令已就位（解密后）的建用户请求。

    用 ``model_copy`` 而不是就地赋值：入参不可变。此处刻意不重跑校验——
    ``password`` 的 8 位下限是给明文提交用的，解密后的强度校验由
    ``validate_password_strength`` 在 service 层统一执行，两处都做会给出
    两套不一致的口径。
    """
    return request.model_copy(
        update={"password": decrypt_transmitted(request.encrypted_password, request.password, request)}
    )
