"""沙箱自建 tmpfs 的每任务边界。

从 ``sandbox_mounts`` 拆出：那边回答"哪些宿主路径可见"，失效表现是越权读写宿主文件；
这边回答"沙箱自己建的几个内存盘各有多大边界"，失效表现是把容器内存吃光。两类失效的
排查方向完全不同，压在一个文件里改一件事会顺手看错另一件。
"""

from __future__ import annotations

_BYTES_PER_MIB = 1024 * 1024
_PRIVATE_HOME = "/tmp/antcode-home"
_PRIVATE_SHARED_MEMORY = "/dev/shm"
_PRIVATE_SHARED_MEMORY_MODE = "1777"

# 能拿到 ``--size`` 的 tmpfs：bwrap 的 ``--size`` 只作用于紧随其后的那一个 ``--tmpfs``。
SIZED_TMPFS_MOUNTS = ("/dev/shm", "/tmp")
# bwrap 同样建了 tmpfs、却没有任何开关能定尺寸的两个挂载点：newroot 自身（``/``）与
# ``--dev`` 建的 ``/dev``。两者都不是 ``--tmpfs`` 建出来的，``--size`` 认不到它们，
# 改由 ``sealed_mount_args`` 封成只读。
SEALED_TMPFS_MOUNTS = ("/dev", "/")
# 沙箱自建 tmpfs 的完整清单，归因侧（``resource_sampler``）按这一份采样。清单与真实
# argv 的一致性由 test_sandbox_unsized_tmpfs_seal 用 argv 反查校验：两边各写一份就会
# 分叉成"限住了却看不见"，那正是本仓反复出现的双真源。
SANDBOX_TMPFS_MOUNTS = (*SIZED_TMPFS_MOUNTS, *SEALED_TMPFS_MOUNTS)

_SEAL_ARGS: tuple[str, ...] = tuple(arg for mount in SEALED_TMPFS_MOUNTS for arg in ("--remount-ro", mount))


def private_namespace_args(tmpfs_size_mb: int) -> list[str]:
    """私有 /dev、/proc 与两个能定尺寸的内存盘（/dev/shm、/tmp）。

    这两个 tmpfs 必须显式定尺寸。不给 ``--size`` 时内核按**宿主内存的一半**建 tmpfs：
    真机实测（宿主 32GB）任务里 ``statvfs`` 报 16046MB，而整个 Worker 容器的
    ``memory.max`` 只有 4096MB——单个任务的一个内存盘就是容器额度的近 4 倍。这是典型的
    "值从宿主算出来"：尺寸的来源与它要约束的对象不在同一个坐标系。

    tmpfs 页计入容器 memory cgroup，却既不进任何进程的 RSS（``write()`` 写下去的页没有
    映射）也不计入 ``RLIMIT_DATA``，所以进程层两道限额都看不见它们。``--size`` 是这两个
    挂载点的每任务边界；另外两个拿不到 ``--size`` 的 tmpfs（``/`` 与 ``/dev``）由
    ``sealed_mount_args`` 封成只读，理由见那里。

    尺寸取本任务自己的内存限额（``effective_memory_limit_mb``）——不是新发明的比例，就是
    同一个已经用来收 RLIMIT_DATA 与 RSS 的数：任务对容器内存的占用，无论走哪条通道，都
    不该超过它自己那一份。

    重复计账（必须知情）：任务池已被 RSS 100% 分光（``task_memory_limit_mb`` = 任务池 /
    并发），所以任何非零的 tmpfs 预算按定义都是超配。封掉 ``/`` 与 ``/dev`` 之后，沙箱里
    可写的内存盘只剩这两个，单任务最坏值才真的等于 RSS + /tmp + /dev/shm = **3.5 倍**
    限额——RSS 那一份的上界是 1.5 倍而不是 1 倍，它由轮询监控兑现，而
    ``monitor_interval._OVERSHOOT_BUDGET_RATIO`` 显式允许 50% 超支。真机口径（限额
    1433MB、容器 4096MB）：1.5×1433 + 2×1433 = 5015MB，仍高于容器 mem_limit，硬顶只有
    cgroup——这是已知的超配，收它要动的是限额与并发的分配比例，不在本函数职责内。
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

    bwrap 的 ``--size`` 只认紧随其后的 ``--tmpfs``。newroot 自身与 ``--dev`` 建的
    ``/dev`` 都不是 ``--tmpfs`` 建的，没有任何开关能给它们定尺寸，内核于是按宿主内存的
    一半建盘。真机实测（192.168.1.250，容器 mem_limit 4096MB、宿主 32GB，uid 1000 与
    生产任务同一个）：两处各报 16046MB；``dd`` 往 ``/`` 写 500MB、往 ``/dev`` 写 300MB
    全部成功，容器 ``memory.current`` 从 112MB 涨到 973MB。这两条路径进程层三样都看不
    见——``write()`` 的页不进 RSS、不计 ``RLIMIT_DATA``，原本也不在
    ``SANDBOX_TMPFS_MOUNTS`` 的采样范围里，连归因都没有。

    封成只读而不是另想办法定尺寸，是因为任务本来就不该往这两个挂载点写：可写点全在别的
    挂载上——工作目录是 bind、``/tmp`` 与 ``/dev/shm`` 是定了尺寸的 tmpfs、HOME 在
    ``/tmp`` 里、``/dev`` 下唯一该写的 ``/dev/shm`` 是独立挂载。``--remount-ro`` 不递归，
    封的只是这两个 tmpfs 本身，上面那些挂载一个都不受影响。

    不拿 ``--size N --tmpfs /dev`` 顶替 ``--dev /dev``：那样得到的是空 tmpfs，
    null/zero/full/random/urandom/tty 与 devpts、ptmx、fd 全要自己重建，而 devpts 只能
    bind 宿主那一个实例——为了给一个任务不该写的挂载点定尺寸，把 pty 隔离退回容器共享，
    换来的隔离比现在弱。

    位置是硬约束：``--remount-ro`` 之后再出现任何挂载参数，bwrap 都要往只读的 newroot
    上建挂载点并直接失败。本函数把"全部挂载参数"当入参收进来再拼，调用方就没有"插在
    中间"的写法。
    """
    return (*mount_args, *_SEAL_ARGS)


def _tmpfs_size_args(tmpfs_size_mb: int) -> tuple[str, ...]:
    """``--size`` 只作用于紧随其后的那一个 ``--tmpfs``（bwrap 语义），故每个挂载点各下一次。

    ``<= 0`` 不是"不限制"而是**接线断了**：``init_worker_config`` 恒把 0 换成自适应或
    默认限额（下界 256MB），``engine/config_update`` 走同一条区间校验，运行期没有合法
    路径能送来 0。旧实现"0 就不下 --size"，于是断掉的接线会安静退回**宿主内存的一半**
    ——防护不是被关掉，是被换成了本模块正要消灭的那个值。
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
