from __future__ import annotations

import contextlib
import select
import socket
import socketserver
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from kubernetes.client import CoreV1Api
from kubernetes.stream import portforward

from .models import CopyRequest, PortForwardMode


class PortForwardError(RuntimeError):
    """Raised when a port-forward transport cannot be established."""


def build_rsync_command(
    request: CopyRequest,
    local_port: int,
    rsync_bin: str,
    module_name: str = "volume",
) -> list[str]:
    destination = request.target.subpath.as_posix().strip(".")
    suffix = f"/{destination}/" if destination else "/"
    return [
        rsync_bin,
        "-a",
        "--delete",
        "--numeric-ids",
        f"{request.source_dir}/",
        f"rsync://127.0.0.1:{local_port}/{module_name}{suffix}",
    ]


def open_port_forward(
    mode: PortForwardMode,
    python_launcher: Callable[[], Any],
    kubectl_launcher: Callable[[], Any],
) -> tuple[str, Any]:
    if mode == "python":
        return ("python", python_launcher())
    if mode == "kubectl":
        return ("kubectl", kubectl_launcher())

    try:
        return ("python", python_launcher())
    except PortForwardError:
        return ("kubectl", kubectl_launcher())


class _ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        remote_socket_factory = self.server.remote_socket_factory  # type: ignore[attr-defined]
        remote = remote_socket_factory()
        sockets = [self.request, remote]
        try:
            while True:
                readable, _, _ = select.select(sockets, [], [], 1)
                if not readable:
                    if any(sock.fileno() == -1 for sock in sockets):
                        return
                    continue
                for current in readable:
                    data = current.recv(65536)
                    if not data:
                        return
                    other = remote if current is self.request else self.request
                    other.sendall(data)
        finally:
            with contextlib.suppress(OSError):
                remote.close()


class _ThreadedTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], remote_socket_factory: Callable[[], socket.socket]):
        self.remote_socket_factory = remote_socket_factory
        super().__init__(server_address, _ProxyHandler)


@contextlib.contextmanager
def python_port_forward(
    core_api: CoreV1Api,
    pod_name: str,
    namespace: str,
    remote_port: int,
) -> Iterator[int]:
    try:
        forward = portforward(
            core_api.connect_get_namespaced_pod_portforward,
            pod_name,
            namespace,
            ports=str(remote_port),
        )
    except Exception as exc:  # noqa: BLE001
        raise PortForwardError(f"Python port-forward failed: {exc}") from exc

    server = _ThreadedTCPServer(("127.0.0.1", 0), lambda: forward.socket(remote_port))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        with contextlib.suppress(Exception):
            forward.close()


@contextlib.contextmanager
def kubectl_port_forward(
    kubectl_bin: str,
    namespace: str,
    pod_name: str,
    local_port: int,
    remote_port: int,
) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        [
            kubectl_bin,
            "port-forward",
            "--namespace",
            namespace,
            f"pod/{pod_name}",
            f"{local_port}:{remote_port}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_local_port(local_port)
        yield process
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


def choose_open_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_local_port(local_port: int, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError):
            with socket.create_connection(("127.0.0.1", local_port), timeout=0.2):
                return
        time.sleep(0.1)
    raise PortForwardError(f"Timed out waiting for local port {local_port} to accept connections")


@contextlib.contextmanager
def open_transport_session(
    mode: PortForwardMode,
    core_api: CoreV1Api,
    kubectl_bin: str,
    namespace: str,
    pod_name: str,
    remote_port: int,
) -> Iterator[tuple[str, int]]:
    local_port = choose_open_local_port()

    if mode in {"auto", "python"}:
        try:
            with python_port_forward(core_api, pod_name, namespace, remote_port) as forwarded_port:
                yield ("python", forwarded_port)
                return
        except PortForwardError:
            if mode == "python":
                raise

    with kubectl_port_forward(
        kubectl_bin=kubectl_bin,
        namespace=namespace,
        pod_name=pod_name,
        local_port=local_port,
        remote_port=remote_port,
    ):
        yield ("kubectl", local_port)


def run_rsync_command(command: list[str]) -> None:
    subprocess.run(command, check=True)


def run_interactive_command(command: list[str]) -> None:
    subprocess.run(command, check=True)
