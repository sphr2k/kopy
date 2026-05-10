from __future__ import annotations

import contextlib
from pathlib import Path

from kopy.models import CopyRequest, Endpoint, TakeoverRequest
from kopy.workflow import run_copy, run_takeover


class FakeKubeClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict]] = []
        self.deleted: list[tuple[str, str]] = []
        self.exec_calls: list[tuple[str, str, str]] = []
        self.created_pvcs: list[tuple[str, dict]] = []
        self.pvcs: dict[tuple[str, str], dict] = {
            ("demo", "media"): {
                "metadata": {"name": "media"},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "50Gi"}},
                    "volumeName": "pv-media",
                },
                "status": {"phase": "Bound"},
            }
        }
        self.pvs: dict[str, dict] = {}
        self.deleted_pvcs: list[tuple[str, str]] = []
        self.patched_pvs: list[tuple[str, dict]] = []

    def create_helper_pod(self, namespace: str, manifest: dict) -> None:
        self.created.append((namespace, manifest))

    def get_pvc(self, namespace: str, pvc_name: str) -> dict | None:
        return self.pvcs.get((namespace, pvc_name))

    def create_pvc(self, namespace: str, manifest: dict) -> None:
        self.created_pvcs.append((namespace, manifest))
        self.pvcs[(namespace, manifest["metadata"]["name"])] = {
            "metadata": {"name": manifest["metadata"]["name"]},
            "spec": manifest["spec"],
            "status": {"phase": "Pending"},
        }

    def get_pv(self, pv_name: str) -> dict:
        return self.pvs[pv_name]

    def delete_pvc(self, namespace: str, pvc_name: str) -> None:
        self.deleted_pvcs.append((namespace, pvc_name))
        self.pvcs.pop((namespace, pvc_name), None)

    def patch_pv(self, pv_name: str, body: dict) -> None:
        self.patched_pvs.append((pv_name, body))

    def wait_for_pvc_bound(self, namespace: str, pvc_name: str, expected_volume_name: str, timeout_seconds: int = 30) -> None:
        return None

    def wait_for_pod_ready(self, namespace: str, pod_name: str, timeout_seconds: int = 30) -> None:
        return None

    def exec_in_pod(self, namespace: str, pod_name: str, command: list[str]) -> str:
        self.exec_calls.append((namespace, pod_name, " ".join(command)))
        script = command[-1]
        if "stat -c '%u:%g'" in script:
            return "2000:2001"
        return ""

    def delete_pod(self, namespace: str, pod_name: str) -> None:
        self.deleted.append((namespace, pod_name))

    def get_pvc_bound_node(self, namespace: str, pvc_name: str) -> str | None:
        return None

    def ensure_pvc_bound(self, namespace: str, pvc_name: str, selected_node: str, timeout_seconds: int = 120) -> None:
        pass


class FakeTransport:
    def __init__(self, transport_name: str = "python", local_port: int = 1873) -> None:
        self.transport_name = transport_name
        self.local_port = local_port
        self.opened: list[tuple[str, str, int, str]] = []

    @contextlib.contextmanager
    def open(self, mode: str, namespace: str, pod_name: str, remote_port: int):
        self.opened.append((mode, namespace, pod_name, remote_port))
        yield self.transport_name, self.local_port


def make_request(keep_pod: bool = False, uid: int | None = None, gid: int | None = None) -> CopyRequest:
    return CopyRequest(
        source=Endpoint(kind="local", resource_name="", path=Path("/tmp/source")),
        target=Endpoint(kind="pvc", resource_name="media", path=Path("uploads")),
        context_name="ctx",
        namespace="demo",
        uid=uid,
        gid=gid,
        keep_pod=keep_pod,
        port_forward_mode="auto",
        create_pvc=False,
        storage_class=None,
    )


def test_run_copy_prepares_target_runs_rsync_and_cleans_up() -> None:
    kube = FakeKubeClient()
    transport = FakeTransport()
    rsync_commands: list[list[str]] = []

    session = run_copy(
        request=make_request(),
        kube=kube,
        helper_image="ghcr.io/example/kopy-agent:latest",
        helper_mount_path="/data",
        rsync_port=1873,
        pod_name_suffix="abcde",
        on_pod_ready=None,
        open_transport=transport.open,
        run_rsync=lambda command: rsync_commands.append(command),
        rsync_bin="rsync",
    )

    assert session.detected_uid == 2000
    assert session.detected_gid == 2001
    assert session.transport == "python"
    assert rsync_commands == [
        [
            "rsync",
            "-a",
            "--delete",
            "--numeric-ids",
            "/tmp/source/",
            "rsync://127.0.0.1:1873/volume/uploads/",
        ]
    ]
    assert kube.deleted == [("demo", session.pod_name)]
    assert any("mkdir -p '/data/uploads'" in call[2] for call in kube.exec_calls)
    assert any("chown -R 2000:2001 '/data/uploads'" in call[2] for call in kube.exec_calls)


def test_run_copy_respects_explicit_uid_gid_without_detection() -> None:
    kube = FakeKubeClient()
    transport = FakeTransport(transport_name="kubectl", local_port=2873)

    session = run_copy(
        request=make_request(uid=123, gid=456),
        kube=kube,
        helper_image="ghcr.io/example/kopy-agent:latest",
        helper_mount_path="/data",
        rsync_port=1873,
        pod_name_suffix="abcde",
        on_pod_ready=None,
        open_transport=transport.open,
        run_rsync=lambda command: None,
        rsync_bin="rsync",
    )

    assert session.detected_uid == 123
    assert session.detected_gid == 456
    assert session.transport == "kubectl"
    assert not any("stat -c '%u:%g'" in call[2] for call in kube.exec_calls)


def test_run_copy_keeps_pod_when_requested() -> None:
    kube = FakeKubeClient()
    transport = FakeTransport()

    run_copy(
        request=make_request(keep_pod=True),
        kube=kube,
        helper_image="ghcr.io/example/kopy-agent:latest",
        helper_mount_path="/data",
        rsync_port=1873,
        pod_name_suffix="abcde",
        on_pod_ready=None,
        open_transport=transport.open,
        run_rsync=lambda command: None,
        rsync_bin="rsync",
    )

    assert kube.deleted == []


def test_run_copy_download_reverses_rsync_direction() -> None:
    kube = FakeKubeClient()
    transport = FakeTransport()
    rsync_commands: list[list[str]] = []

    request = CopyRequest(
        source=Endpoint(kind="pvc", resource_name="media", path=Path("uploads")),
        target=Endpoint(kind="local", resource_name="", path=Path("/tmp/dest")),
        context_name="ctx",
        namespace="demo",
        uid=None,
        gid=None,
        keep_pod=False,
        port_forward_mode="auto",
        create_pvc=False,
        storage_class=None,
    )

    run_copy(
        request=request,
        kube=kube,
        helper_image="ghcr.io/example/kopy-agent:latest",
        helper_mount_path="/data",
        rsync_port=1873,
        pod_name_suffix="abcde",
        on_pod_ready=None,
        open_transport=transport.open,
        run_rsync=lambda command: rsync_commands.append(command),
        rsync_bin="rsync",
    )

    assert rsync_commands == [
        [
            "rsync",
            "-a",
            "--delete",
            "--numeric-ids",
            "rsync://127.0.0.1:1873/volume/uploads/",
            "/tmp/dest/",
        ]
    ]


def test_run_copy_pvc_to_pvc_annotates_pending_target() -> None:
    bound_calls: list[tuple[str, str, str]] = []

    class FakeKubeWithNode(FakeKubeClient):
        def get_pvc_bound_node(self, namespace: str, pvc_name: str) -> str | None:
            return "my-node" if pvc_name == "source-pvc" else None

        def ensure_pvc_bound(self, namespace: str, pvc_name: str, selected_node: str, timeout_seconds: int = 120) -> None:
            bound_calls.append((namespace, pvc_name, selected_node))

    request = CopyRequest(
        source=Endpoint(kind="pvc", resource_name="source-pvc", path=Path(".")),
        target=Endpoint(kind="pvc", resource_name="target-pvc", path=Path(".")),
        context_name="ctx",
        namespace="demo",
        uid=None,
        gid=None,
        keep_pod=False,
        port_forward_mode="auto",
        create_pvc=False,
        storage_class=None,
    )
    kube = FakeKubeWithNode()
    kube.pvcs[("demo", "target-pvc")] = {
        "metadata": {"name": "target-pvc"},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "50Gi"}},
            "volumeName": "pv-target",
        },
        "status": {"phase": "Pending"},
    }

    run_copy(
        request=request,
        kube=kube,
        helper_image="ghcr.io/example/kopy-agent:latest",
        helper_mount_path="/data",
        rsync_port=1873,
        pod_name_suffix="abcde",
        on_pod_ready=None,
        open_transport=FakeTransport().open,
        run_rsync=lambda command: None,
        rsync_bin="rsync",
    )

    assert bound_calls == [("demo", "target-pvc", "my-node")]


def test_run_copy_pvc_to_pvc_uses_exec() -> None:
    kube = FakeKubeClient()
    transport = FakeTransport()
    kube.pvcs[("demo", "target-pvc")] = {
        "metadata": {"name": "target-pvc"},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "50Gi"}},
            "volumeName": "pv-target",
        },
        "status": {"phase": "Bound"},
    }

    request = CopyRequest(
        source=Endpoint(kind="pvc", resource_name="source-pvc", path=Path(".")),
        target=Endpoint(kind="pvc", resource_name="target-pvc", path=Path(".")),
        context_name="ctx",
        namespace="demo",
        uid=None,
        gid=None,
        keep_pod=False,
        port_forward_mode="auto",
        create_pvc=False,
        storage_class=None,
    )

    session = run_copy(
        request=request,
        kube=kube,
        helper_image="ghcr.io/example/kopy-agent:latest",
        helper_mount_path="/data",
        rsync_port=1873,
        pod_name_suffix="abcde",
        on_pod_ready=None,
        open_transport=transport.open,
        run_rsync=lambda command: None,
        rsync_bin="rsync",
    )

    assert session.transport == "exec"
    assert session.local_port is None
    assert transport.opened == []  # no port-forward for pvc→pvc
    assert any("rsync" in " ".join(call[2].split()) for call in kube.exec_calls)


def test_run_copy_creates_missing_target_pvc_when_requested() -> None:
    kube = FakeKubeClient()
    kube.pvcs[("demo", "source-pvc")] = {
        "metadata": {"name": "source-pvc"},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "50Gi"}},
            "volumeName": "pv-source",
        },
        "status": {"phase": "Bound"},
    }
    request = CopyRequest(
        source=Endpoint(kind="pvc", resource_name="source-pvc", path=Path(".")),
        target=Endpoint(kind="pvc", resource_name="target-pvc", path=Path(".")),
        context_name="ctx",
        namespace="demo",
        uid=None,
        gid=None,
        keep_pod=False,
        port_forward_mode="auto",
        create_pvc=True,
        storage_class="fast-ssd",
    )

    run_copy(
        request=request,
        kube=kube,
        helper_image="ghcr.io/example/kopy-agent:latest",
        helper_mount_path="/data",
        rsync_port=1873,
        pod_name_suffix="abcde",
        on_pod_ready=None,
        open_transport=FakeTransport().open,
        run_rsync=lambda command: None,
        rsync_bin="rsync",
    )

    assert kube.created_pvcs == [
        (
            "demo",
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": "target-pvc"},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "50Gi"}},
                    "storageClassName": "fast-ssd",
                },
            },
        )
    ]


def test_run_copy_requires_create_flag_for_missing_target_pvc() -> None:
    kube = FakeKubeClient()
    kube.pvcs[("demo", "source-pvc")] = {
        "metadata": {"name": "source-pvc"},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "50Gi"}},
            "volumeName": "pv-source",
        },
        "status": {"phase": "Bound"},
    }
    request = CopyRequest(
        source=Endpoint(kind="pvc", resource_name="source-pvc", path=Path(".")),
        target=Endpoint(kind="pvc", resource_name="target-pvc", path=Path(".")),
        context_name="ctx",
        namespace="demo",
        uid=None,
        gid=None,
        keep_pod=False,
        port_forward_mode="auto",
        create_pvc=False,
        storage_class=None,
    )

    try:
        run_copy(
            request=request,
            kube=kube,
            helper_image="ghcr.io/example/kopy-agent:latest",
            helper_mount_path="/data",
            rsync_port=1873,
            pod_name_suffix="abcde",
            on_pod_ready=None,
            open_transport=FakeTransport().open,
            run_rsync=lambda command: None,
            rsync_bin="rsync",
        )
    except ValueError as exc:
        assert "Target PVC demo/target-pvc does not exist" in str(exc)
    else:
        raise AssertionError("expected missing target PVC to fail")


def test_run_takeover_rebinds_migrated_volume_to_original_name() -> None:
    kube = FakeKubeClient()
    kube.pvcs[("demo", "media-migrated")] = {
        "metadata": {"name": "media-migrated"},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "50Gi"}},
            "storageClassName": "fast-ssd",
            "volumeMode": "Filesystem",
            "volumeName": "pv-migrated",
        },
        "status": {"phase": "Bound"},
    }
    kube.pvcs[("demo", "media")] = {
        "metadata": {"name": "media"},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "50Gi"}},
            "storageClassName": "slow-hdd",
            "volumeName": "pv-original",
        },
        "status": {"phase": "Bound"},
    }
    kube.pvs["pv-migrated"] = {
        "metadata": {
            "name": "pv-migrated",
            "annotations": {
                "pv.kubernetes.io/bind-completed": "yes",
                "pv.kubernetes.io/bound-by-controller": "yes",
            },
        },
        "spec": {"persistentVolumeReclaimPolicy": "Retain"},
    }
    kube.pvs["pv-original"] = {
        "metadata": {"name": "pv-original", "annotations": {}},
        "spec": {"persistentVolumeReclaimPolicy": "Retain"},
    }

    session = run_takeover(
        request=TakeoverRequest(
            source=Endpoint(kind="pvc", resource_name="media-migrated", path=Path(".")),
            target=Endpoint(kind="pvc", resource_name="media", path=Path(".")),
            context_name="ctx",
            namespace="demo",
            set_retain=False,
        ),
        kube=kube,
    )

    assert session.namespace == "demo"
    assert session.pvc_name == "media"
    assert session.pv_name == "pv-migrated"
    assert kube.deleted_pvcs == [("demo", "media"), ("demo", "media-migrated")]
    assert kube.patched_pvs == [
        (
            "pv-migrated",
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
    ]
    assert kube.created_pvcs[-1] == (
        "demo",
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "media"},
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "50Gi"}},
                "storageClassName": "fast-ssd",
                "volumeMode": "Filesystem",
                "volumeName": "pv-migrated",
            },
        },
    )


def test_run_takeover_requires_retain_reclaim_policy() -> None:
    kube = FakeKubeClient()
    kube.pvcs[("demo", "media-migrated")] = {
        "metadata": {"name": "media-migrated"},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "50Gi"}},
            "volumeName": "pv-migrated",
        },
        "status": {"phase": "Bound"},
    }
    kube.pvcs[("demo", "media")] = {
        "metadata": {"name": "media"},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "50Gi"}},
            "volumeName": "pv-original",
        },
        "status": {"phase": "Bound"},
    }
    kube.pvs["pv-migrated"] = {
        "metadata": {"name": "pv-migrated", "annotations": {}},
        "spec": {"persistentVolumeReclaimPolicy": "Delete"},
    }
    kube.pvs["pv-original"] = {
        "metadata": {"name": "pv-original", "annotations": {}},
        "spec": {"persistentVolumeReclaimPolicy": "Retain"},
    }

    try:
        run_takeover(
            request=TakeoverRequest(
                source=Endpoint(kind="pvc", resource_name="media-migrated", path=Path(".")),
                target=Endpoint(kind="pvc", resource_name="media", path=Path(".")),
                context_name="ctx",
                namespace="demo",
                set_retain=False,
            ),
            kube=kube,
        )
    except ValueError as exc:
        assert "Retain" in str(exc)
    else:
        raise AssertionError("expected non-Retain takeover to fail")


def test_run_takeover_can_temporarily_set_and_restore_retain() -> None:
    kube = FakeKubeClient()
    kube.pvcs[("demo", "media-migrated")] = {
        "metadata": {"name": "media-migrated"},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "50Gi"}},
            "storageClassName": "fast-ssd",
            "volumeMode": "Filesystem",
            "volumeName": "pv-migrated",
        },
        "status": {"phase": "Bound"},
    }
    kube.pvcs[("demo", "media")] = {
        "metadata": {"name": "media"},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "50Gi"}},
            "volumeName": "pv-original",
        },
        "status": {"phase": "Bound"},
    }
    kube.pvs["pv-migrated"] = {
        "metadata": {
            "name": "pv-migrated",
            "annotations": {
                "pv.kubernetes.io/bind-completed": "yes",
                "pv.kubernetes.io/bound-by-controller": "yes",
            },
        },
        "spec": {"persistentVolumeReclaimPolicy": "Delete"},
    }
    kube.pvs["pv-original"] = {
        "metadata": {"name": "pv-original", "annotations": {}},
        "spec": {"persistentVolumeReclaimPolicy": "Recycle"},
    }

    run_takeover(
        request=TakeoverRequest(
            source=Endpoint(kind="pvc", resource_name="media-migrated", path=Path(".")),
            target=Endpoint(kind="pvc", resource_name="media", path=Path(".")),
            context_name="ctx",
            namespace="demo",
            set_retain=True,
        ),
        kube=kube,
    )

    assert kube.patched_pvs == [
        ("pv-migrated", {"spec": {"persistentVolumeReclaimPolicy": "Retain"}}),
        ("pv-original", {"spec": {"persistentVolumeReclaimPolicy": "Retain"}}),
        (
            "pv-migrated",
            {
                "metadata": {
                    "annotations": {
                        "pv.kubernetes.io/bind-completed": None,
                        "pv.kubernetes.io/bound-by-controller": None,
                    }
                },
                "spec": {"claimRef": None},
            },
        ),
        ("pv-migrated", {"spec": {"persistentVolumeReclaimPolicy": "Delete"}}),
        ("pv-original", {"spec": {"persistentVolumeReclaimPolicy": "Recycle"}}),
    ]
