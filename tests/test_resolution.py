from pathlib import Path

from kopy.cli import resolve_target
from kopy.models import TargetRef


class FakeClient:
    def list_pvcs(self, namespace: str) -> list[str]:
        assert namespace == "demo"
        return ["alpha", "media"]


def test_resolve_target_returns_explicit_target_without_picker() -> None:
    target = resolve_target(
        client=FakeClient(),
        namespace="demo",
        target=TargetRef(kind="pvc", resource_name="media", subpath=Path("uploads")),
        interactive=True,
        picker=lambda items, prompt, multi=False: ["alpha"],
    )

    assert target.resource_name == "media"


def test_resolve_target_uses_picker_when_pvc_name_missing() -> None:
    target = resolve_target(
        client=FakeClient(),
        namespace="demo",
        target=TargetRef(kind="pvc", resource_name="", subpath=Path(".")),
        interactive=True,
        picker=lambda items, prompt, multi=False: ["media"],
    )

    assert target.resource_name == "media"
    assert target.subpath == Path(".")
