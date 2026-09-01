"""沙箱自建 tmpfs 的每任务尺寸边界（``sandbox_mounts`` 管的是"哪些宿主路径可见"）。"""

from __future__ import annotations

_BYTES_PER_MIB = 1024 * 1024
_PRIVATE_HOME = "/tmp/antcode-home"
_PRIVATE_SHARED_MEMORY = "/dev/shm"
_PRIVATE_SHARED_MEMORY_MODE = "1777"

SIZED_TMPFS_MOUNTS = ("/dev/shm", "/tmp")
# bwrap 同样建了 tmpfs、却没有任何开关能定尺寸的两个挂载点：newroot 自身（``/``）与
# ``--dev`` 建的 ``/dev``。两者都不是 ``--tmpfs`` 建出来的，``--size`` 认不到它们，
# 改由 ``sealed_mount_args`` 封成只读。
SEALED_TMPFS_MOUNTS = ("/dev", "/")
# 归因侧（``resource_sampler``）按这一份采样；与真实 argv 的一致性由
# test_sandbox_unsized_tmpfs_seal 用 argv 反查校验，两边各写一份就会分叉成
# "限住了却看不见"。
SANDBOX_TMPFS_MOUNTS = (*SIZED_TMPFS_MOUNTS, *SEALED_TMPFS_MOUNTS)

_SEAL_ARGS: tuple[str, ...] = tuple(arg for mount in SEALED_TMPFS_MOUNTS for arg in ("--remount-ro", mount))


def private_namespace_args(tmpfs_size_mb: int) -> list[str]:
    """私有 /dev、/proc 与两个能定尺寸的内存盘（/dev/shm、/tmp）。

    这两个 tmpfs 必须显式定尺寸：不给 ``--size`` 时内核按**宿主内存的一半**建盘，真机
    实测（宿主 32GB）任务里 ``statvfs`` 报 16046MB，而整个 Worker 容器的 ``memory.max``
    只有 4096MB。tmpfs 页计入容器 memory cgroup，却既不进 RSS（``write()`` 写下去的页
    没有映射）也不计入 ``RLIMIT_DATA``，进程层两道限额都看不见它们。

    尺寸取本任务自己的 ``effective_memory_limit_mb``，与 RLIMIT_DATA、RSS 监控同源。

    已知超配（必须知情）：任务池已被 RSS 100% 分光，任何非零 tmpfs 预算按定义都是超配。
    单任务最坏值 = RSS + /tmp + /dev/shm = 3.5 倍限额（RSS 那份上界是 1.5 倍，因为
    ``monitor_interval._OVERSHOOT_BUDGET_RATIO`` 显式允许 50% 超支）。真机口径（限额
    1433MB、容器 4096MB）：1.5×1433 + 2×1433 = 5015MB 仍高于容器 mem_limit，硬顶只有
    cgroup。收它要动限额与并发的分配比例，不在本函数职责内。
    """
    return [
        "--dev",
        "/dev",
        "--dir",
        _PRIVATE_SHARED_MEMORY,
        *_tmpfs_size_args(tmpfs_size_mb),
        "--tmpfs",
        _PRIVATE_SHARED_MEMORY,
        "--chmod",
        _PRIVATE_SHARED_MEMORY_MODE,
        _PRIVATE_SHARED_MEMORY,
        "--proc",
        "/proc",
        *_tmpfs_size_args(tmpfs_size_mb),
        "--tmpfs",
        "/tmp",
        "--dir",
        _PRIVATE_HOME,
    ]


def sealed_mount_args(mount_args: tuple[str, ...]) -> tuple[str, ...]:
    """在全部挂载参数之后，把拿不到 ``--size`` 的两个 tmpfs 封成只读。

    真机实测（容器 mem_limit 4096MB、宿主 32GB）：``/`` 与 ``/dev`` 各报 16046MB，
    ``dd`` 往 ``/`` 写 500MB、往 ``/dev`` 写 300MB 全部成功，容器 ``memory.current``
    从 112MB 涨到 973MB，且进程层 RSS / ``RLIMIT_DATA`` 都看不见。

    封成只读而不是另想办法定尺寸：任务本来就不该往这两处写，可写点全在别的挂载上
    （工作目录 bind、``/tmp`` 与 ``/dev/shm`` 是定了尺寸的 tmpfs、HOME 在 ``/tmp``
    里）。``--remount-ro`` 不递归，那些挂载一个都不受影响。

    不拿 ``--size N --tmpfs /dev`` 顶替 ``--dev /dev``：那样得到的是空 tmpfs，
    null/zero/full/random/urandom/tty 与 devpts、ptmx、fd 全要自己重建，而 devpts 只能
    bind 宿主那一个实例——为了给一个任务不该写的挂载点定尺寸，把 pty 隔离退回容器共享。

    位置是硬约束：``--remount-ro`` 之后再出现任何挂载参数，bwrap 都要往只读的 newroot
    上建挂载点并直接失败。本函数把"全部挂载参数"当入参收进来再拼，调用方就没有"插在
    中间"的写法。
    """
    return (*mount_args, *_SEAL_ARGS)


def _tmpfs_size_args(tmpfs_size_mb: int) -> tuple[str, ...]:
    """``--size`` 只作用于紧随其后的那一个 ``--tmpfs``（bwrap 语义），故每个挂载点各下一次。

    ``<= 0`` 不是"不限制"而是**接线断了**：``init_worker_config`` 恒把 0 换成自适应或
    默认限额（下界 256MB），``engine/config_update`` 走同一条区间校验，运行期没有合法
    路径能送来 0。旧实现"0 就不下 --size"，于是断掉的接线会安静退回**宿主内存的一半**。
    """
    if tmpfs_size_mb <= 0:
        raise RuntimeError(
            f"沙箱内存盘尺寸不可知({tmpfs_size_mb})：0/负数不表示不限制，而是任务级内存限额没接上。"
            "此时不下 --size 会让内核按宿主内存的一半建 /tmp 与 /dev/shm，单个任务的一个内存盘"
            "就能超出整个容器的额度。请检查资源限额接线"
            "(config.init_worker_config → ExecutorConfig.default_memory_limit_mb → effective_memory_limit_mb)。"
        )
    return ("--size", str(tmpfs_size_mb * _BYTES_PER_MIB))


def private_home() -> str:
    """Return the writable HOME created inside every sandbox namespace."""
    return _PRIVATE_HOME


__all__ = [
    "SANDBOX_TMPFS_MOUNTS",
    "SEALED_TMPFS_MOUNTS",
    "SIZED_TMPFS_MOUNTS",
    "private_home",
    "private_namespace_args",
    "sealed_mount_args",
]
