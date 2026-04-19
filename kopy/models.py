from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


TargetKind = Literal["pvc"]
PortForwardMode = Literal["auto", "python", "kubectl"]


@dataclass(frozen=True)
class TargetRef:
    kind: TargetKind
    resource_name: str
    subpath: Path


@dataclass(frozen=True)
class CopyRequest:
    source_dir: Path
    context_name: str | None
    namespace: str | None
    target: TargetRef
    uid: int | None
    gid: int | None
    keep_pod: bool
    port_forward_mode: PortForwardMode


@dataclass(frozen=True)
class DebugRequest:
    context_name: str | None
    namespace: str | None
    target: TargetRef
    keep_pod: bool
    shell: str


@dataclass(frozen=True)
class CopySession:
    pod_name: str
    namespace: str
    local_port: int
    rsync_port: int
    detected_uid: int | None
    detected_gid: int | None
    transport: str


@dataclass(frozen=True)
class DebugSession:
    pod_name: str
    namespace: str
