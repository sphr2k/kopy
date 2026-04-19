from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    helper_image: str = os.getenv("KOPY_HELPER_IMAGE", "instrumentisto/rsync-ssh:latest")
    helper_mount_path: str = os.getenv("KOPY_HELPER_MOUNT_PATH", "/data")
    helper_rsync_port: int = int(os.getenv("KOPY_HELPER_RSYNC_PORT", "1873"))
    kubectl_bin: str = os.getenv("KOPY_KUBECTL_BIN", "kubectl")
    rsync_bin: str = os.getenv("KOPY_RSYNC_BIN", "rsync")
    port_forward_timeout_seconds: int = int(os.getenv("KOPY_PORT_FORWARD_TIMEOUT", "5"))


settings = Settings()
