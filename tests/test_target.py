from pathlib import Path

import pytest

from kopy.target import TargetParseError, parse_target


def test_parse_pvc_target_without_subpath() -> None:
    target = parse_target("pvc://media")

    assert target.kind == "pvc"
    assert target.resource_name == "media"
    assert target.subpath == Path(".")


def test_parse_pvc_target_with_nested_subpath() -> None:
    target = parse_target("pvc://media/uploads/2026-04")

    assert target.kind == "pvc"
    assert target.resource_name == "media"
    assert target.subpath == Path("uploads/2026-04")


def test_parse_pvc_target_without_name_keeps_selection_open() -> None:
    target = parse_target("pvc://")

    assert target.kind == "pvc"
    assert target.resource_name == ""
    assert target.subpath == Path(".")


@pytest.mark.parametrize(
    "raw_target",
    [
        "pod://demo/tmp",
        "media",
        "pvc://media/../../etc",
    ],
)
def test_parse_target_rejects_invalid_values(raw_target: str) -> None:
    with pytest.raises(TargetParseError):
        parse_target(raw_target)
