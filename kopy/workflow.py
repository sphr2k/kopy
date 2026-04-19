from __future__ import annotations

import random
import string
from pathlib import Path

from .k8s import build_debug_pod_manifest, build_helper_pod_manifest
from .models import CopyRequest, CopySession, DebugRequest, DebugSession
from .transport import build_rsync_command


def resolve_destination_ownership(
    requested_subpath: Path,
    path_stats: dict[str, tuple[int, int]],
) -> tuple[int, int]:
    current = requested_subpath
    while True:
        key = current.as_posix()
        if key in path_stats:
            return path_stats[key]
        if current == Path(".") or not current.parts:
            break
        current = current.parent

    raise ValueError(
        f"Unable to detect ownership for destination path '{requested_subpath.as_posix()}'. "
        "Pass --uid and --gid explicitly."
    )


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _sanitize_name_part(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")
    return cleaned or "target"


def build_helper_pod_name(prefix: str, resource_name: str, suffix: str | None = None) -> str:
    effective_suffix = suffix or "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(5))
    base = f"kopy-{prefix}-{_sanitize_name_part(resource_name)}"
    max_base_length = 63 - 1 - len(effective_suffix)
    return f"{base[:max_base_length].rstrip('-')}-{effective_suffix}"


def detect_destination_ownership(
    kube: object,
    namespace: str,
    pod_name: str,
    mount_path: str,
    subpath: Path,
) -> tuple[int, int]:
    relative = "" if subpath == Path(".") else "/" + subpath.as_posix()
    script = f"""
set -eu
target="{mount_path}{relative}"
root="{mount_path}"
if [ -e "$target" ]; then
  stat -c '%u:%g' "$target"
  exit 0
fi
while [ "$target" != "{mount_path}" ] && [ ! -e "$target" ]; do
  target="$(dirname "$target")"
done
if [ ! -e "$target" ]; then
  exit 42
fi
stat -c '%u:%g' "$target"
""".strip()
    output = kube.exec_in_pod(namespace, pod_name, ["sh", "-c", script])  # type: ignore[attr-defined]
    uid_text, gid_text = output.strip().split(":", 1)
    return int(uid_text), int(gid_text)


def prepare_destination(
    kube: object,
    namespace: str,
    pod_name: str,
    mount_path: str,
    subpath: Path,
    uid: int,
    gid: int,
) -> None:
    destination = mount_path if subpath == Path(".") else f"{mount_path}/{subpath.as_posix()}"
    script = f"set -eu; mkdir -p {_shell_quote(destination)}; chown -R {uid}:{gid} {_shell_quote(destination)}"
    kube.exec_in_pod(namespace, pod_name, ["sh", "-c", script])  # type: ignore[attr-defined]


def run_copy(
    request: CopyRequest,
    kube: object,
    helper_image: str,
    helper_mount_path: str,
    rsync_port: int,
    pod_name_suffix: str | None,
    on_pod_ready: object | None,
    open_transport: object,
    run_rsync: object,
    rsync_bin: str,
) -> CopySession:
    namespace = request.namespace
    if not namespace:
        raise ValueError("Copy request is missing a namespace")

    pod_name = build_helper_pod_name("copy", request.target.resource_name, suffix=pod_name_suffix)
    manifest = build_helper_pod_manifest(
        request=request,
        pod_name=pod_name,
        image=helper_image,
        rsync_port=rsync_port,
        mount_path=helper_mount_path,
    )
    kube.create_helper_pod(namespace, manifest)  # type: ignore[attr-defined]
    try:
        kube.wait_for_pod_ready(namespace, pod_name, timeout_seconds=30)  # type: ignore[attr-defined]
        if on_pod_ready is not None:
            on_pod_ready(pod_name)
        if request.uid is not None and request.gid is not None:
            uid, gid = request.uid, request.gid
        else:
            uid, gid = detect_destination_ownership(
                kube=kube,
                namespace=namespace,
                pod_name=pod_name,
                mount_path=helper_mount_path,
                subpath=request.target.subpath,
            )
        prepare_destination(
            kube=kube,
            namespace=namespace,
            pod_name=pod_name,
            mount_path=helper_mount_path,
            subpath=request.target.subpath,
            uid=uid,
            gid=gid,
        )
        with open_transport(
            request.port_forward_mode,
            namespace,
            pod_name,
            rsync_port,
        ) as (transport_name, local_port):
            command = build_rsync_command(
                request=request,
                local_port=local_port,
                rsync_bin=rsync_bin,
            )
            run_rsync(command)
            return CopySession(
                pod_name=pod_name,
                namespace=namespace,
                local_port=local_port,
                rsync_port=rsync_port,
                detected_uid=uid,
                detected_gid=gid,
                transport=transport_name,
            )
    finally:
        if not request.keep_pod:
            kube.delete_pod(namespace, pod_name)  # type: ignore[attr-defined]


def run_debug_session(
    request: DebugRequest,
    kube: object,
    helper_image: str,
    helper_mount_path: str,
    kubectl_bin: str,
    pod_name_suffix: str | None,
    on_pod_ready: object | None,
    attach_shell: object,
) -> DebugSession:
    namespace = request.namespace
    if not namespace:
        raise ValueError("Debug request is missing a namespace")

    pod_name = build_helper_pod_name("debug", request.target.resource_name, suffix=pod_name_suffix)
    manifest = build_debug_pod_manifest(
        request=request,
        pod_name=pod_name,
        image=helper_image,
        mount_path=helper_mount_path,
    )
    kube.create_helper_pod(namespace, manifest)  # type: ignore[attr-defined]
    try:
        kube.wait_for_pod_ready(namespace, pod_name, timeout_seconds=30)  # type: ignore[attr-defined]
        if on_pod_ready is not None:
            on_pod_ready(pod_name)
        attach_shell(
            [
                kubectl_bin,
                "exec",
                "-it",
                "--namespace",
                namespace,
                pod_name,
                "--",
                request.shell,
            ]
        )
        return DebugSession(pod_name=pod_name, namespace=namespace)
    finally:
        if not request.keep_pod:
            kube.delete_pod(namespace, pod_name)  # type: ignore[attr-defined]
