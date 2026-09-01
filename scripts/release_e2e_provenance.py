"""五个应用镜像的来源归属：本轮跑的到底是不是本轮构建出来的镜像。

原判据（release_e2e_orchestrator._validate_exact_images）对这五个镜像是**恒真**的：
期望值来自 `application_images(tag)`，Compose 侧的 `${ANTCODE_IMAGE_TAG}` 同样来自那个
tag，两边同源，最多只能验出 Compose 文件把镜像名写错。真机上撞到过 `:gateway-e2e`
构建于被测提交之前、镜像里新模块零命中——`SKIP_BUILD=1` 复用它，整轮 E2E 能对着旧
代码报"7/7 全过"。

这里把"名字对得上"换成两件可证伪的事实：

* `[2/7]` 构建完立刻记下五个镜像的 **image ID**，`[4/7]` 要求 Compose 解析出来的引用
  仍然指向同一个 ID。字符串换成内容摘要，中途被谁重打一次 tag 就会被抓住。
* 记下这一轮**有没有真的构建**。构建过 ⇒ 镜像出自本工作树，这是构造性的事实；
  没构建过 ⇒ 在不改 Dockerfile（本次改动面之外）的前提下，镜像与提交之间不存在任何
  可验证的绑定——build 时打进 label 的 source_ref 只是构建者自报的字符串，工作树是脏
  的它照样"对"。所以复用镜像的那一轮**不作为发布门禁**，并把每个镜像的 ID 与创建时间
  原样打出来，让运维一眼看见自己在复用什么。
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from scripts.release_e2e_environment import APP_SERVICES

PROVENANCE_FILE = "build-provenance.json"
RELEASE_IMAGES_FILE = "release-images.json"
INSPECT_FORMAT = "{{.Id}}\t{{.Created}}"


@dataclass(frozen=True)
class ImageIdentity:
    """镜像的内容标识与创建时间；ID 是 Compose 引用解析后的唯一可比对象。"""

    identifier: str
    created: str


def inspect_image(reference: str) -> ImageIdentity:
    command = ("docker", "image", "inspect", "--format", INSPECT_FORMAT, reference)
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"无法解析镜像 {reference}（docker image inspect exit={result.returncode}）: {result.stderr}"
        )
    identifier, created = result.stdout.strip().split("\t")
    return ImageIdentity(identifier=identifier, created=created)


def _application_references(state_dir: Path) -> dict[str, str]:
    """五个应用镜像的引用只在 `release_e2e_environment.application_images` 定义一份。"""
    images = json.loads((state_dir / RELEASE_IMAGES_FILE).read_text(encoding="utf-8"))
    return {service: images[service] for service in APP_SERVICES}


def record(state_dir: Path, *, built_this_run: bool) -> dict[str, ImageIdentity]:
    identities = {
        service: inspect_image(reference) for service, reference in _application_references(state_dir).items()
    }
    payload = {
        "built_this_run": built_this_run,
        "images": {service: asdict(identity) for service, identity in identities.items()},
    }
    (state_dir / PROVENANCE_FILE).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return identities


def validate(state_dir: Path, compose_images: dict[str, str]) -> None:
    """Compose 解析出的应用镜像引用必须仍指向 `[2/7]` 记下的那一个 image ID。"""
    payload = json.loads((state_dir / PROVENANCE_FILE).read_text(encoding="utf-8"))
    for service, recorded in payload["images"].items():
        observed = inspect_image(compose_images[service])
        if observed.identifier != recorded["identifier"]:
            raise RuntimeError(
                f"应用镜像已不是本轮构建的那一个: {service} 记录={recorded['identifier']} 当前={observed.identifier}"
            )


def _report(identities: dict[str, ImageIdentity], *, built_this_run: bool) -> str:
    lines = [f"{service}: {identity.identifier} created={identity.created}" for service, identity in identities.items()]
    if built_this_run:
        return "\n".join(["[2/7] 本轮构建的应用镜像（发布门禁按它们判定）：", *lines])
    return "\n".join(
        [
            "[2/7] 复用了未经本轮构建的应用镜像：本轮不构成发布门禁，只能当调试轮次看。",
            "      发布前请去掉 ANTCODE_GATEWAY_E2E_SKIP_BUILD 重跑一轮完整构建。",
            *lines,
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--reused", action="store_true", help="本轮跳过了构建（ANTCODE_GATEWAY_E2E_SKIP_BUILD=1）")
    arguments = parser.parse_args()
    built_this_run = not arguments.reused
    identities = record(arguments.state_dir, built_this_run=built_this_run)
    print(_report(identities, built_this_run=built_this_run))


if __name__ == "__main__":
    main()
