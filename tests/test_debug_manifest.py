from pathlib import Path

from kopy.k8s import build_debug_pod_manifest
from kopy.models import Endpoint


def test_debug_pod_manifest_mounts_requested_pvc_and_sleeps() -> None:
    manifest = build_debug_pod_manifest(
        pvc_endpoint=Endpoint(kind="pvc", resource_name="plex-config", path=Path("debug")),
        pod_name="kopy-debug-123",
        image="ghcr.io/example/kopy-agent:latest",
    )

    container = manifest["spec"]["containers"][0]
    assert manifest["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "plex-config"
    assert container["securityContext"]["runAsUser"] == 0
    assert container["command"] == ["sh", "-c", "mkdir -p /data/debug && exec sleep infinity"]
