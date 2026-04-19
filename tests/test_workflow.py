from pathlib import Path

from kopy.k8s import build_helper_pod_manifest
from kopy.models import CopyRequest, TargetRef
from kopy.workflow import resolve_destination_ownership


def make_request(subpath: str = ".") -> CopyRequest:
    return CopyRequest(
        source_dir=Path("/tmp/source"),
        context_name="ctx",
        namespace="demo",
        target=TargetRef(kind="pvc", resource_name="media", subpath=Path(subpath)),
        uid=None,
        gid=None,
        keep_pod=False,
        port_forward_mode="auto",
    )


def test_helper_pod_manifest_mounts_requested_pvc_and_subpath() -> None:
    manifest = build_helper_pod_manifest(
        request=make_request("uploads/2026-04"),
        pod_name="kopy-copy-123",
        image="ghcr.io/example/kopy-agent:latest",
        rsync_port=1873,
    )

    container = manifest["spec"]["containers"][0]
    assert manifest["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "media"
    assert container["securityContext"]["runAsUser"] == 0
    assert container["env"][-1] == {"name": "KOPY_DESTINATION_SUBPATH", "value": "uploads/2026-04"}


def test_resolve_destination_ownership_prefers_existing_subpath() -> None:
    ownership = resolve_destination_ownership(
        requested_subpath=Path("uploads/2026-04"),
        path_stats={
            "uploads/2026-04": (1000, 1001),
            "uploads": (2000, 2001),
        },
    )

    assert ownership == (1000, 1001)


def test_resolve_destination_ownership_prefers_existing_named_subdirectory_over_root() -> None:
    ownership = resolve_destination_ownership(
        requested_subpath=Path("Library"),
        path_stats={
            ".": (501, 0),
            "Library": (1000, 1000),
        },
    )

    assert ownership == (1000, 1000)


def test_resolve_destination_ownership_walks_up_to_existing_parent() -> None:
    ownership = resolve_destination_ownership(
        requested_subpath=Path("uploads/2026-04"),
        path_stats={"uploads": (2000, 2001)},
    )

    assert ownership == (2000, 2001)


def test_resolve_destination_ownership_fails_when_nothing_exists() -> None:
    try:
        resolve_destination_ownership(
            requested_subpath=Path("uploads/2026-04"),
            path_stats={},
        )
    except ValueError as exc:
        assert "Unable to detect ownership" in str(exc)
    else:
        raise AssertionError("expected ownership detection to fail")
