from pathlib import Path

from kopy.k8s import build_helper_pod_manifest, build_pvc_manifest
from kopy.models import CopyRequest, Endpoint
from kopy.workflow import resolve_destination_ownership


def make_request(subpath: str = ".") -> CopyRequest:
    return CopyRequest(
        source=Endpoint(kind="local", resource_name="", path=Path("/tmp/source")),
        target=Endpoint(kind="pvc", resource_name="media", path=Path(subpath)),
        context_name="ctx",
        namespace="demo",
        uid=None,
        gid=None,
        keep_pod=False,
        port_forward_mode="auto",
        create_pvc=False,
        storage_class=None,
    )


def test_helper_pod_manifest_mounts_requested_pvc_and_subpath() -> None:
    request = make_request("uploads/2026-04")
    manifest = build_helper_pod_manifest(
        pvc_endpoint=request.target,
        pod_name="kopy-copy-123",
        image="ghcr.io/example/kopy-agent:latest",
        rsync_port=1873,
    )

    container = manifest["spec"]["containers"][0]
    assert manifest["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "media"
    assert container["securityContext"]["runAsUser"] == 0
    assert container["env"][-1] == {"name": "KOPY_DESTINATION_SUBPATH", "value": "uploads/2026-04"}
    assert manifest["spec"]["tolerations"] == [{"operator": "Exists"}]


def test_build_pvc_manifest_uses_source_shape_and_explicit_storage_class() -> None:
    manifest = build_pvc_manifest(
        pvc_name="media-migrated",
        access_modes=["ReadWriteOnce"],
        requested_storage="50Gi",
        storage_class_name="fast-ssd",
    )

    assert manifest["metadata"]["name"] == "media-migrated"
    assert manifest["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert manifest["spec"]["resources"]["requests"]["storage"] == "50Gi"
    assert manifest["spec"]["storageClassName"] == "fast-ssd"


def test_build_pvc_manifest_omits_storage_class_when_unspecified() -> None:
    manifest = build_pvc_manifest(
        pvc_name="media-migrated",
        access_modes=["ReadWriteMany"],
        requested_storage="200Gi",
        storage_class_name=None,
    )

    assert manifest["spec"]["accessModes"] == ["ReadWriteMany"]
    assert manifest["spec"]["resources"]["requests"]["storage"] == "200Gi"
    assert "storageClassName" not in manifest["spec"]


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
