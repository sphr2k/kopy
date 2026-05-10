from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


TargetKind = Literal["pvc", "local"]
PortForwardMode = Literal["auto", "python", "kubectl"]


@dataclass(frozen=True)
class Endpoint:
    kind: TargetKind
    resource_name: str  # PVC name; empty for local
    path: Path  # subpath within PVC mount, or local filesystem path


@dataclass(frozen=True)
class CopyRequest:
    source: Endpoint
    target: Endpoint
    context_name: str | None
    namespace: str | None
    uid: int | None
    gid: int | None
    keep_pod: bool
    port_forward_mode: PortForwardMode
    create_pvc: bool
    storage_class: str | None


@dataclass(frozen=True)
class DebugRequest:
    context_name: str | None
    namespace: str | None
    target: Endpoint
    keep_pod: bool
    shell: str


@dataclass(frozen=True)
class TakeoverRequest:
    source: Endpoint
    target: Endpoint
    context_name: str | None
    namespace: str | None
    set_retain: bool


@dataclass(frozen=True)
class CopySession:
    pod_name: str
    namespace: str
    local_port: int | None
    rsync_port: int | None
    detected_uid: int | None
    detected_gid: int | None
    transport: str


@dataclass(frozen=True)
class DebugSession:
    pod_name: str
    namespace: str


@dataclass(frozen=True)
class TakeoverSession:
    namespace: str
    pvc_name: str
    pv_name: str
