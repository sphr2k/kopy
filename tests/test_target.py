from pathlib import Path

import pytest

from kopy.target import EndpointParseError, parse_endpoint


def test_parse_pvc_target_without_subpath() -> None:
    ep = parse_endpoint("pvc://media")

    assert ep.kind == "pvc"
    assert ep.resource_name == "media"
    assert ep.path == Path(".")


def test_parse_pvc_target_with_nested_subpath() -> None:
    ep = parse_endpoint("pvc://media/uploads/2026-04")

    assert ep.kind == "pvc"
    assert ep.resource_name == "media"
    assert ep.path == Path("uploads/2026-04")


def test_parse_pvc_target_without_name_keeps_selection_open() -> None:
    ep = parse_endpoint("pvc://")

    assert ep.kind == "pvc"
    assert ep.resource_name == ""
    assert ep.path == Path(".")


def test_parse_local_relative_path() -> None:
    ep = parse_endpoint("./some/dir")

    assert ep.kind == "local"
    assert ep.resource_name == ""
    assert ep.path == Path("./some/dir")


def test_parse_local_absolute_path() -> None:
    ep = parse_endpoint("/abs/path")

    assert ep.kind == "local"
    assert ep.resource_name == ""
    assert ep.path == Path("/abs/path")


def test_parse_local_bare_name() -> None:
    ep = parse_endpoint("somedir")

    assert ep.kind == "local"
    assert ep.resource_name == ""
    assert ep.path == Path("somedir")


@pytest.mark.parametrize(
    "raw_target",
    [
        "pod://demo/tmp",
        "pvc://media/../../etc",
    ],
)
def test_parse_endpoint_rejects_invalid_values(raw_target: str) -> None:
    with pytest.raises(EndpointParseError):
        parse_endpoint(raw_target)
