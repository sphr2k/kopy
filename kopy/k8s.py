from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from kubernetes import client, config
from kubernetes.stream import stream

from .models import CopyRequest, DebugRequest


def build_helper_pod_manifest(
    request: CopyRequest,
    pod_name: str,
    image: str,
    rsync_port: int,
    mount_path: str = "/data",
) -> dict[str, Any]:
    destination_subpath = request.target.subpath.as_posix()
    shell_command = f"""
set -eu
mkdir -p /tmp/kopy
cat > /tmp/kopy/rsyncd.conf <<'EOF'
uid = 0
gid = 0
use chroot = false
max connections = 1
log file = /dev/stdout
[volume]
path = {mount_path}
read only = false
EOF
exec rsync --daemon --no-detach --config=/tmp/kopy/rsyncd.conf --port={rsync_port}
""".strip()

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "labels": {
                "app.kubernetes.io/name": "kopy",
                "app.kubernetes.io/component": "copy-helper",
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "copy-agent",
                    "image": image,
                    "command": ["sh", "-c", shell_command],
                    "ports": [{"containerPort": rsync_port, "name": "rsync"}],
                    "env": [
                        {"name": "KOPY_DESTINATION_PATH", "value": mount_path},
                        {"name": "KOPY_DESTINATION_SUBPATH", "value": destination_subpath},
                    ],
                    "securityContext": {
                        "runAsUser": 0,
                        "runAsGroup": 0,
                    },
                    "volumeMounts": [{"name": "target-volume", "mountPath": mount_path}],
                }
            ],
            "volumes": [
                {
                    "name": "target-volume",
                    "persistentVolumeClaim": {"claimName": request.target.resource_name},
                }
            ],
        },
    }


def build_debug_pod_manifest(
    request: DebugRequest,
    pod_name: str,
    image: str,
    mount_path: str = "/data",
) -> dict[str, Any]:
    destination_subpath = request.target.subpath.as_posix()
    prepare_path = mount_path if destination_subpath == "." else f"{mount_path}/{destination_subpath}"
    shell_command = f"mkdir -p {prepare_path} && exec sleep infinity"

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "labels": {
                "app.kubernetes.io/name": "kopy",
                "app.kubernetes.io/component": "debug-helper",
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "debug-shell",
                    "image": image,
                    "command": ["sh", "-c", shell_command],
                    "env": [
                        {"name": "KOPY_DESTINATION_PATH", "value": mount_path},
                        {"name": "KOPY_DESTINATION_SUBPATH", "value": destination_subpath},
                    ],
                    "securityContext": {
                        "runAsUser": 0,
                        "runAsGroup": 0,
                    },
                    "volumeMounts": [{"name": "target-volume", "mountPath": mount_path}],
                }
            ],
            "volumes": [
                {
                    "name": "target-volume",
                    "persistentVolumeClaim": {"claimName": request.target.resource_name},
                }
            ],
        },
    }


@dataclass
class KubeClient:
    context_name: str | None
    kubeconfig: str | None = None

    _core_api: client.CoreV1Api = field(init=False, repr=False)
    _contexts: list[dict[str, Any]] = field(init=False, repr=False, default_factory=list)
    _active_context_name: str | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        kube_cfg = client.Configuration()
        contexts, active_context = config.list_kube_config_contexts(
            config_file=str(Path(self.kubeconfig).expanduser()) if self.kubeconfig else None
        )
        config.load_kube_config(
            config_file=str(Path(self.kubeconfig).expanduser()) if self.kubeconfig else None,
            context=self.context_name,
            client_configuration=kube_cfg,
        )
        self._contexts = cast(list[dict[str, Any]], contexts or [])
        active_name = cast(dict[str, Any] | None, active_context)
        self._active_context_name = None if active_name is None else cast(str | None, active_name.get("name"))
        self._core_api = client.CoreV1Api(client.ApiClient(kube_cfg))

    def current_context_name(self) -> str | None:
        return self.context_name or self._active_context_name

    def current_namespace(self) -> str | None:
        wanted = self.current_context_name()
        for context in self._contexts:
            if context.get("name") != wanted:
                continue
            raw_context = cast(dict[str, Any], context.get("context", {}))
            return cast(str | None, raw_context.get("namespace"))
        return None

    def list_pvcs(self, namespace: str) -> list[str]:
        items = self._core_api.list_namespaced_persistent_volume_claim(namespace=namespace).items
        return sorted(item.metadata.name for item in items if item.metadata and item.metadata.name)

    def create_helper_pod(self, namespace: str, manifest: dict[str, Any]) -> client.V1Pod:
        return cast(client.V1Pod, self._core_api.create_namespaced_pod(namespace=namespace, body=manifest))

    def get_pod(self, namespace: str, pod_name: str) -> client.V1Pod:
        return cast(client.V1Pod, self._core_api.read_namespaced_pod(name=pod_name, namespace=namespace))

    def delete_pod(self, namespace: str, pod_name: str) -> None:
        self._core_api.delete_namespaced_pod(name=pod_name, namespace=namespace)

    def exec_in_pod(self, namespace: str, pod_name: str, command: list[str]) -> str:
        return cast(
            str,
            stream(
                self._core_api.connect_get_namespaced_pod_exec,
                pod_name,
                namespace,
                command=command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
            ),
        )

    def wait_for_pod_ready(self, namespace: str, pod_name: str, timeout_seconds: int = 30) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            pod = self.get_pod(namespace=namespace, pod_name=pod_name)
            status = pod.status
            if status and status.phase == "Running":
                conditions = status.conditions or []
                if any(condition.type == "Ready" and condition.status == "True" for condition in conditions):
                    return
            time.sleep(1)
        raise TimeoutError(f"Timed out waiting for pod {namespace}/{pod_name} to become Ready")
