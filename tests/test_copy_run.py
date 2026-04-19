from __future__ import annotations

import contextlib
from pathlib import Path

from kopy.models import CopyRequest, TargetRef
from kopy.workflow import run_copy


class FakeKubeClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict]] = []
        self.deleted: list[tuple[str, str]] = []
        self.exec_calls: list[tuple[str, str, str]] = []

    def create_helper_pod(self, namespace: str, manifest: dict) -> None:
        self.created.append((namespace, manifest))

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
        source_dir=Path("/tmp/source"),
        context_name="ctx",
        namespace="demo",
        target=TargetRef(kind="pvc", resource_name="media", subpath=Path("uploads")),
        uid=uid,
        gid=gid,
        keep_pod=keep_pod,
        port_forward_mode="auto",
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
