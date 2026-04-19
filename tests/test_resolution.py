from pathlib import Path

from kopy.cli import resolve_endpoint
from kopy.models import Endpoint


class FakeClient:
    def list_pvcs(self, namespace: str) -> list[str]:
        assert namespace == "demo"
        return ["alpha", "media"]


def test_resolve_endpoint_returns_explicit_pvc_without_picker() -> None:
    ep = resolve_endpoint(
        client=FakeClient(),
        namespace="demo",
        endpoint=Endpoint(kind="pvc", resource_name="media", path=Path("uploads")),
        interactive=True,
        picker=lambda items, prompt, multi=False: ["alpha"],
    )

    assert ep.resource_name == "media"


def test_resolve_endpoint_uses_picker_when_pvc_name_missing() -> None:
    ep = resolve_endpoint(
        client=FakeClient(),
        namespace="demo",
        endpoint=Endpoint(kind="pvc", resource_name="", path=Path(".")),
        interactive=True,
        picker=lambda items, prompt, multi=False: ["media"],
    )

    assert ep.resource_name == "media"
    assert ep.path == Path(".")


def test_resolve_endpoint_passes_through_local_endpoint() -> None:
    ep = resolve_endpoint(
        client=FakeClient(),
        namespace="demo",
        endpoint=Endpoint(kind="local", resource_name="", path=Path("./data")),
        interactive=True,
        picker=lambda items, prompt, multi=False: [],
    )

    assert ep.kind == "local"
    assert ep.path == Path("./data")
