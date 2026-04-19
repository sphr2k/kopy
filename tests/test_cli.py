from pathlib import Path

from kopy.cli import build_copy_request, build_debug_request


def test_build_copy_request_defaults_to_root_subpath() -> None:
    request = build_copy_request(
        source_dir=Path("/tmp/source"),
        raw_target="pvc://media",
        context_name=None,
        namespace="demo",
        uid=None,
        gid=None,
        keep_pod=False,
        port_forward_mode="auto",
    )

    assert request.source_dir == Path("/tmp/source")
    assert request.namespace == "demo"
    assert request.target.resource_name == "media"
    assert request.target.subpath == Path(".")
    assert request.keep_pod is False
    assert request.port_forward_mode == "auto"


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
    assert request.target.subpath == Path("debug")
    assert request.keep_pod is True
    assert request.shell == "bash"
