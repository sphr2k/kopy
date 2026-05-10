from pathlib import Path

from kopy.cli import build_copy_request, build_debug_request, build_takeover_request


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
        create_pvc=False,
        storage_class=None,
    )

    assert request.source.kind == "local"
    assert request.source.path == Path("/tmp/source")
    assert request.namespace == "demo"
    assert request.target.resource_name == "media"
    assert request.target.path == Path(".")
    assert request.keep_pod is False
    assert request.port_forward_mode == "auto"
    assert request.create_pvc is False
    assert request.storage_class is None


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
        create_pvc=False,
        storage_class=None,
    )

    assert request.source.kind == "pvc"
    assert request.source.resource_name == "media"
    assert request.source.path == Path("uploads")
    assert request.target.kind == "local"
    assert request.target.path == Path("./local-dest")


def test_build_copy_request_supports_target_pvc_creation() -> None:
    request = build_copy_request(
        raw_source="pvc://media/uploads",
        raw_target="pvc://media-migrated/uploads",
        context_name="ctx",
        namespace="demo",
        uid=None,
        gid=None,
        keep_pod=True,
        port_forward_mode="kubectl",
        create_pvc=True,
        storage_class="fast-ssd",
    )

    assert request.create_pvc is True
    assert request.storage_class == "fast-ssd"
    assert request.target.resource_name == "media-migrated"


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


def test_build_takeover_request_preserves_endpoints() -> None:
    request = build_takeover_request(
        raw_source="pvc://media-migrated",
        raw_target="pvc://media",
        context_name="ctx",
        namespace="demo",
        set_retain=True,
    )

    assert request.context_name == "ctx"
    assert request.namespace == "demo"
    assert request.source.resource_name == "media-migrated"
    assert request.target.resource_name == "media"
    assert request.source.path == Path(".")
    assert request.target.path == Path(".")
    assert request.set_retain is True
