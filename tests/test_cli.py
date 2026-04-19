from pathlib import Path

from kopy.cli import build_copy_request, build_debug_request


def test_build_copy_request_defaults_to_root_subpath() -> None:
    request = build_copy_request(
        raw_source="/tmp/source",
        raw_target="pvc://media",
        context_name=None,
        namespace="demo",
        uid=None,
        gid=None,
        keep_pod=False,
        port_forward_mode="auto",
    )

    assert request.source.kind == "local"
    assert request.source.path == Path("/tmp/source")
    assert request.namespace == "demo"
    assert request.target.resource_name == "media"
    assert request.target.path == Path(".")
    assert request.keep_pod is False
    assert request.port_forward_mode == "auto"


def test_build_copy_request_pvc_source() -> None:
    request = build_copy_request(
        raw_source="pvc://media/uploads",
        raw_target="./local-dest",
        context_name=None,
        namespace="demo",
        uid=None,
        gid=None,
        keep_pod=False,
        port_forward_mode="auto",
    )

    assert request.source.kind == "pvc"
    assert request.source.resource_name == "media"
    assert request.source.path == Path("uploads")
    assert request.target.kind == "local"
    assert request.target.path == Path("./local-dest")


def test_build_debug_request_preserves_shell_and_target() -> None:
    request = build_debug_request(
        raw_target="pvc://media/debug",
        context_name="ctx",
        namespace="demo",
        keep_pod=True,
        shell="bash",
    )

    assert request.context_name == "ctx"
    assert request.namespace == "demo"
    assert request.target.resource_name == "media"
    assert request.target.path == Path("debug")
    assert request.keep_pod is True
    assert request.shell == "bash"
