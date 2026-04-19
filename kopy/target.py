from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .models import TargetRef


class TargetParseError(ValueError):
    """Raised when a target URI cannot be parsed."""


def _normalize_subpath(raw_subpath: str) -> Path:
    normalized = raw_subpath.strip("/")
    if not normalized:
        return Path(".")

    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise TargetParseError(f"Invalid target subpath: {raw_subpath}")
    return path


def parse_target(raw_target: str) -> TargetRef:
    parsed = urlparse(raw_target)
    if parsed.scheme != "pvc":
        raise TargetParseError(f"Unsupported target scheme: {parsed.scheme or '<missing>'}")

    return TargetRef(
        kind="pvc",
        resource_name=parsed.netloc,
        subpath=_normalize_subpath(parsed.path),
    )
