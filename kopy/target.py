from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .models import Endpoint


class EndpointParseError(ValueError):
    """Raised when an endpoint URI cannot be parsed."""


def _normalize_subpath(raw_subpath: str) -> Path:
    normalized = raw_subpath.strip("/")
    if not normalized:
        return Path(".")

    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise EndpointParseError(f"Invalid target subpath: {raw_subpath}")
    return path


def parse_endpoint(raw: str) -> Endpoint:
    parsed = urlparse(raw)
    if parsed.scheme == "pvc":
        return Endpoint(
            kind="pvc",
            resource_name=parsed.netloc,
            path=_normalize_subpath(parsed.path),
        )
    if parsed.scheme == "":
        return Endpoint(kind="local", resource_name="", path=Path(raw))
    raise EndpointParseError(f"Unsupported endpoint scheme: {parsed.scheme!r}")
