from __future__ import annotations

from pathlib import Path

from kopy.models import DebugRequest, TargetRef
from kopy.workflow import run_debug_session


class FakeKubeClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict]] = []
        self.deleted: list[tuple[str, str]] = []

    def create_helper_pod(self, namespace: str, manifest: dict) -> None:
        self.created.append((namespace, manifest))

    def wait_for_pod_ready(self, namespace: str, pod_name: str, timeout_seconds: int = 30) -> None:
        return None

    def delete_pod(self, namespace: str, pod_name: str) -> None:
        self.deleted.append((namespace, pod_name))


def make_request(keep_pod: bool = False) -> DebugRequest:
    return DebugRequest(
        context_name="ctx",
        namespace="plex",
        target=TargetRef(kind="pvc", resource_name="plex-config", subpath=Path("debug")),
        keep_pod=keep_pod,
        shell="sh",
    )


def test_run_debug_session_attaches_and_cleans_up() -> None:
    kube = FakeKubeClient()
    attach_calls: list[list[str]] = []

    session = run_debug_session(
        request=make_request(),
        kube=kube,
        helper_image="ghcr.io/example/kopy-agent:latest",
        helper_mount_path="/data",
        kubectl_bin="kubectl",
        pod_name_suffix="abcde",
        on_pod_ready=None,
        attach_shell=lambda command: attach_calls.append(command),
    )

    assert session.namespace == "plex"
    assert session.pod_name == "kopy-debug-plex-config-abcde"
    assert attach_calls == [
        [
            "kubectl",
            "exec",
            "-it",
            "--namespace",
            "plex",
            "kopy-debug-plex-config-abcde",
            "--",
            "sh",
        ]
    ]
    assert kube.deleted == [("plex", "kopy-debug-plex-config-abcde")]


def test_run_debug_session_keeps_pod_when_requested() -> None:
    kube = FakeKubeClient()

    run_debug_session(
        request=make_request(keep_pod=True),
        kube=kube,
        helper_image="ghcr.io/example/kopy-agent:latest",
        helper_mount_path="/data",
        kubectl_bin="kubectl",
        pod_name_suffix="abcde",
        on_pod_ready=None,
        attach_shell=lambda command: None,
    )

    assert kube.deleted == []
