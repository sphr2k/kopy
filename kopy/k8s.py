from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from kubernetes import client, config
from kubernetes.stream import stream

from .models import DebugRequest, Endpoint


HELPER_POD_TOLERATIONS = [{"operator": "Exists"}]


def build_pvc_manifest(
    pvc_name: str,
    access_modes: list[str],
    requested_storage: str,
    storage_class_name: str | None,
    volume_name: str | None = None,
    volume_mode: str | None = None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "accessModes": access_modes,
        "resources": {"requests": {"storage": requested_storage}},
    }
    if storage_class_name is not None:
        spec["storageClassName"] = storage_class_name
    if volume_name is not None:
        spec["volumeName"] = volume_name
    if volume_mode is not None:
        spec["volumeMode"] = volume_mode
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": pvc_name},
        "spec": spec,
    }


def build_helper_pod_manifest(
    pvc_endpoint: Endpoint,
    pod_name: str,
    image: str,
    rsync_port: int,
    mount_path: str = "/data",
) -> dict[str, Any]:
    destination_subpath = pvc_endpoint.path.as_posix()
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
            "tolerations": HELPER_POD_TOLERATIONS,
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
                    "persistentVolumeClaim": {"claimName": pvc_endpoint.resource_name},
                }
            ],
        },
    }


def build_debug_pod_manifest(
    pvc_endpoint: Endpoint,
    pod_name: str,
    image: str,
    mount_path: str = "/data",
) -> dict[str, Any]:
    destination_subpath = pvc_endpoint.path.as_posix()
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
            "tolerations": HELPER_POD_TOLERATIONS,
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
                    "persistentVolumeClaim": {"claimName": pvc_endpoint.resource_name},
                }
            ],
        },
    }


def build_dual_pvc_pod_manifest(
    source_pvc: str,
    target_pvc: str,
    source_mount: str,
    target_mount: str,
    image: str,
    pod_name: str,
) -> dict[str, Any]:
    shell_command = f"mkdir -p {source_mount} {target_mount} && exec sleep infinity"
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "labels": {
                "app.kubernetes.io/name": "kopy",
                "app.kubernetes.io/component": "pvc-copy-helper",
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "tolerations": HELPER_POD_TOLERATIONS,
            "containers": [
                {
                    "name": "copy-agent",
                    "image": image,
                    "command": ["sh", "-c", shell_command],
                    "securityContext": {
                        "runAsUser": 0,
                        "runAsGroup": 0,
                    },
                    "volumeMounts": [
                        {"name": "source-volume", "mountPath": source_mount},
                        {"name": "target-volume", "mountPath": target_mount},
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "source-volume",
                    "persistentVolumeClaim": {"claimName": source_pvc},
                },
                {
                    "name": "target-volume",
                    "persistentVolumeClaim": {"claimName": target_pvc},
                },
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

    def get_pvc(self, namespace: str, pvc_name: str) -> dict[str, Any] | None:
        try:
            pvc = self._core_api.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
        except client.ApiException as exc:
            if exc.status == 404:
                return None
            raise
        return cast(dict[str, Any], self._core_api.api_client.sanitize_for_serialization(pvc))

    def create_pvc(self, namespace: str, manifest: dict[str, Any]) -> dict[str, Any]:
        pvc = self._core_api.create_namespaced_persistent_volume_claim(namespace=namespace, body=manifest)
        return cast(dict[str, Any], self._core_api.api_client.sanitize_for_serialization(pvc))

    def delete_pvc(self, namespace: str, pvc_name: str) -> None:
        self._core_api.delete_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)

    def get_pv(self, pv_name: str) -> dict[str, Any]:
        pv = self._core_api.read_persistent_volume(name=pv_name)
        return cast(dict[str, Any], self._core_api.api_client.sanitize_for_serialization(pv))

    def patch_pv(self, pv_name: str, body: dict[str, Any]) -> dict[str, Any]:
        pv = self._core_api.patch_persistent_volume(name=pv_name, body=body)
        return cast(dict[str, Any], self._core_api.api_client.sanitize_for_serialization(pv))

    def get_pvc_bound_node(self, namespace: str, pvc_name: str) -> str | None:
        """Return the kubernetes.io/hostname of the node a bound PVC's PV is affined to, if any."""
        pvc = self._core_api.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
        volume_name = pvc.spec and pvc.spec.volume_name
        if not volume_name:
            return None
        pv = self._core_api.read_persistent_volume(name=volume_name)
        required = pv.spec and pv.spec.node_affinity and pv.spec.node_affinity.required
        if not required or not required.node_selector_terms:
            return None
        for term in required.node_selector_terms:
            for expr in term.match_expressions or []:
                if expr.key == "kubernetes.io/hostname" and expr.operator == "In" and expr.values:
                    return expr.values[0]
        return None

    def ensure_pvc_bound(self, namespace: str, pvc_name: str, selected_node: str, timeout_seconds: int = 120) -> None:
        """Annotate a pending PVC with selected-node to trigger WaitForFirstConsumer provisioning, then wait for it to bind.

        Fails fast if the CSI provisioner emits a ProvisioningFailed event.
        """
        pvc = self._core_api.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
        if pvc.status and pvc.status.phase == "Bound":
            return
        self._core_api.patch_namespaced_persistent_volume_claim(
            name=pvc_name,
            namespace=namespace,
            body={"metadata": {"annotations": {"volume.kubernetes.io/selected-node": selected_node}}},
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            pvc = self._core_api.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
            if pvc.status and pvc.status.phase == "Bound":
                return
            events = self._core_api.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={pvc_name},involvedObject.kind=PersistentVolumeClaim,reason=ProvisioningFailed",
            )
            if events.items:
                msg = events.items[-1].message or "provisioning failed"
                raise ValueError(f"Target PVC {namespace}/{pvc_name} cannot be provisioned: {msg}")
            time.sleep(2)
        raise TimeoutError(f"Timed out waiting for PVC {namespace}/{pvc_name} to bind")

    def create_helper_pod(self, namespace: str, manifest: dict[str, Any]) -> client.V1Pod:
        return cast(client.V1Pod, self._core_api.create_namespaced_pod(namespace=namespace, body=manifest))

    def wait_for_pvc_bound(self, namespace: str, pvc_name: str, expected_volume_name: str, timeout_seconds: int = 30) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            pvc = self._core_api.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
            if pvc.status and pvc.status.phase == "Bound":
                actual = pvc.spec.volume_name if pvc.spec else None
                if actual == expected_volume_name:
                    return
            time.sleep(1)
        raise TimeoutError(
            f"Timed out waiting for PVC {namespace}/{pvc_name} to bind to volume {expected_volume_name}"
        )

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
