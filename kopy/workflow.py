from __future__ import annotations

import random
import string
from pathlib import Path

from .k8s import build_debug_pod_manifest, build_dual_pvc_pod_manifest, build_helper_pod_manifest, build_pvc_manifest
from .models import CopyRequest, CopySession, DebugRequest, DebugSession, TakeoverRequest, TakeoverSession
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


def ensure_target_pvc(
    request: CopyRequest,
    kube: object,
) -> None:
    if request.target.kind != "pvc":
        return

    namespace = request.namespace
    assert namespace
    existing = kube.get_pvc(namespace, request.target.resource_name)  # type: ignore[attr-defined]
    if existing is not None:
        return

    if not request.create_pvc:
        raise ValueError(f"Target PVC {namespace}/{request.target.resource_name} does not exist")
    if request.source.kind != "pvc":
        raise ValueError("Target PVC creation currently requires a PVC source to copy access modes and size")

    source_pvc = kube.get_pvc(namespace, request.source.resource_name)  # type: ignore[attr-defined]
    if source_pvc is None:
        raise ValueError(f"Source PVC {namespace}/{request.source.resource_name} does not exist")

    source_spec = source_pvc.get("spec", {})
    access_modes = source_spec.get("accessModes")
    requested_storage = source_spec.get("resources", {}).get("requests", {}).get("storage")
    if not access_modes or not requested_storage:
        raise ValueError(
            f"Source PVC {namespace}/{request.source.resource_name} is missing access modes or requested storage"
        )

    manifest = build_pvc_manifest(
        pvc_name=request.target.resource_name,
        access_modes=access_modes,
        requested_storage=requested_storage,
        storage_class_name=request.storage_class,
    )
    kube.create_pvc(namespace, manifest)  # type: ignore[attr-defined]


def _require_root_pvc_endpoint(endpoint: object, role: str) -> None:
    resource_name = endpoint.resource_name  # type: ignore[attr-defined]
    path = endpoint.path  # type: ignore[attr-defined]
    kind = endpoint.kind  # type: ignore[attr-defined]
    if kind != "pvc" or path != Path("."):
        raise ValueError(f"{role} must be a PVC root endpoint like pvc://{resource_name or '<name>'}")


def _update_reclaim_policy(kube: object, pv_name: str, reclaim_policy: str) -> None:
    kube.patch_pv(pv_name, {"spec": {"persistentVolumeReclaimPolicy": reclaim_policy}})  # type: ignore[attr-defined]


def run_takeover(
    request: TakeoverRequest,
    kube: object,
) -> TakeoverSession:
    namespace = request.namespace
    if not namespace:
        raise ValueError("Takeover request is missing a namespace")

    _require_root_pvc_endpoint(request.source, "Source")
    _require_root_pvc_endpoint(request.target, "Target")

    source_pvc = kube.get_pvc(namespace, request.source.resource_name)  # type: ignore[attr-defined]
    target_pvc = kube.get_pvc(namespace, request.target.resource_name)  # type: ignore[attr-defined]
    if source_pvc is None:
        raise ValueError(f"Source PVC {namespace}/{request.source.resource_name} does not exist")
    if target_pvc is None:
        raise ValueError(f"Target PVC {namespace}/{request.target.resource_name} does not exist")

    source_spec = source_pvc.get("spec", {})
    target_spec = target_pvc.get("spec", {})
    source_pv_name = source_spec.get("volumeName")
    target_pv_name = target_spec.get("volumeName")
    if not source_pv_name or not target_pv_name:
        raise ValueError("Both PVCs must already be bound to PVs before takeover")

    source_pv = kube.get_pv(source_pv_name)  # type: ignore[attr-defined]
    target_pv = kube.get_pv(target_pv_name)  # type: ignore[attr-defined]
    reclaim_policies = {
        source_pv_name: source_pv.get("spec", {}).get("persistentVolumeReclaimPolicy"),
        target_pv_name: target_pv.get("spec", {}).get("persistentVolumeReclaimPolicy"),
    }
    patched_pvs: list[tuple[str, str]] = []
    for pv_name, reclaim_policy in reclaim_policies.items():
        if reclaim_policy == "Retain":
            continue
        if not request.set_retain:
            raise ValueError(
                f"PV {pv_name} must use Retain reclaim policy before takeover to avoid data loss"
            )
        _update_reclaim_policy(kube, pv_name, "Retain")
        patched_pvs.append((pv_name, reclaim_policy))

    try:
        kube.delete_pvc(namespace, request.target.resource_name)  # type: ignore[attr-defined]
        kube.delete_pvc(namespace, request.source.resource_name)  # type: ignore[attr-defined]
        kube.patch_pv(  # type: ignore[attr-defined]
            source_pv_name,
            {
                "metadata": {
                    "annotations": {
                        "pv.kubernetes.io/bind-completed": None,
                        "pv.kubernetes.io/bound-by-controller": None,
                    }
                },
                "spec": {"claimRef": None},
            },
        )
        manifest = build_pvc_manifest(
            pvc_name=request.target.resource_name,
            access_modes=source_spec.get("accessModes", []),
            requested_storage=source_spec.get("resources", {}).get("requests", {}).get("storage"),
            storage_class_name=source_spec.get("storageClassName"),
            volume_name=source_pv_name,
            volume_mode=source_spec.get("volumeMode"),
        )
        kube.create_pvc(namespace, manifest)  # type: ignore[attr-defined]
        if hasattr(kube, "wait_for_pvc_bound"):
            kube.wait_for_pvc_bound(namespace, request.target.resource_name, source_pv_name)  # type: ignore[attr-defined]
        return TakeoverSession(namespace=namespace, pvc_name=request.target.resource_name, pv_name=source_pv_name)
    finally:
        for pv_name, original_policy in patched_pvs:
            _update_reclaim_policy(kube, pv_name, original_policy)


def _run_pvc_to_pvc(
    request: CopyRequest,
    kube: object,
    helper_image: str,
    rsync_bin: str,
    pod_name_suffix: str | None,
    on_pod_ready: object | None,
) -> CopySession:
    namespace = request.namespace
    assert namespace
    source = request.source
    target = request.target
    source_mount = "/kopy-source"
    target_mount = "/kopy-target"

    pod_name = build_helper_pod_name(
        "p2p",
        f"{source.resource_name}-{target.resource_name}",
        suffix=pod_name_suffix,
    )
    source_node = kube.get_pvc_bound_node(namespace, source.resource_name)  # type: ignore[attr-defined]
    if source_node:
        kube.ensure_pvc_bound(namespace, target.resource_name, selected_node=source_node)  # type: ignore[attr-defined]
    manifest = build_dual_pvc_pod_manifest(
        source_pvc=source.resource_name,
        target_pvc=target.resource_name,
        source_mount=source_mount,
        target_mount=target_mount,
        image=helper_image,
        pod_name=pod_name,
    )
    kube.create_helper_pod(namespace, manifest)  # type: ignore[attr-defined]
    try:
        kube.wait_for_pod_ready(namespace, pod_name, timeout_seconds=30)  # type: ignore[attr-defined]
        if on_pod_ready is not None:
            on_pod_ready(pod_name)

        src_subpath = source.path.as_posix()
        dst_subpath = target.path.as_posix()
        src = source_mount if src_subpath == "." else f"{source_mount}/{src_subpath}"
        dst = target_mount if dst_subpath == "." else f"{target_mount}/{dst_subpath}"

        kube.exec_in_pod(namespace, pod_name, [rsync_bin, "-a", "--delete", "--numeric-ids", f"{src}/", f"{dst}/"])  # type: ignore[attr-defined]

        return CopySession(
            pod_name=pod_name,
            namespace=namespace,
            local_port=None,
            rsync_port=None,
            detected_uid=None,
            detected_gid=None,
            transport="exec",
        )
    finally:
        if not request.keep_pod:
            kube.delete_pod(namespace, pod_name)  # type: ignore[attr-defined]


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
    source = request.source
    target = request.target
    namespace = request.namespace
    if not namespace:
        raise ValueError("Copy request is missing a namespace")

    if source.kind == "local" and target.kind == "local":
        raise ValueError("Both endpoints are local — use cp or rsync directly")

    if target.kind == "pvc":
        ensure_target_pvc(request, kube)

    if source.kind == "pvc" and target.kind == "pvc":
        return _run_pvc_to_pvc(
            request=request,
            kube=kube,
            helper_image=helper_image,
            rsync_bin=rsync_bin,
            pod_name_suffix=pod_name_suffix,
            on_pod_ready=on_pod_ready,
        )

    # One local, one pvc
    pvc_ep = target if target.kind == "pvc" else source
    is_upload = source.kind == "local"

    pod_name = build_helper_pod_name("copy", pvc_ep.resource_name, suffix=pod_name_suffix)
    manifest = build_helper_pod_manifest(
        pvc_endpoint=pvc_ep,
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

        if is_upload:
            if request.uid is not None and request.gid is not None:
                uid, gid = request.uid, request.gid
            else:
                uid, gid = detect_destination_ownership(
                    kube=kube,
                    namespace=namespace,
                    pod_name=pod_name,
                    mount_path=helper_mount_path,
                    subpath=target.path,
                )
            prepare_destination(
                kube=kube,
                namespace=namespace,
                pod_name=pod_name,
                mount_path=helper_mount_path,
                subpath=target.path,
                uid=uid,
                gid=gid,
            )
        else:
            uid, gid = request.uid, request.gid
            target.path.mkdir(parents=True, exist_ok=True)

        subpath_str = pvc_ep.path.as_posix()
        suffix = f"/{subpath_str}/" if subpath_str != "." else "/"

        with open_transport(request.port_forward_mode, namespace, pod_name, rsync_port) as (transport_name, local_port):  # type: ignore[attr-defined]
            rsync_url = f"rsync://127.0.0.1:{local_port}/volume{suffix}"
            if is_upload:
                rsync_src = f"{source.path}/"
                rsync_dst = rsync_url
            else:
                rsync_src = rsync_url
                rsync_dst = f"{target.path}/"

            command = build_rsync_command(source=rsync_src, dest=rsync_dst, rsync_bin=rsync_bin)
            run_rsync(command)  # type: ignore[operator]
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
        pvc_endpoint=request.target,
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
