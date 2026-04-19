from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from clypi import ClypiConfig, Command, Spin, Spinner, Styler, Theme, arg, configure
from rich.panel import Panel

from .k8s import KubeClient
from .models import CopyRequest, DebugRequest, PortForwardMode, TargetRef
from .settings import settings
from .target import parse_target
from .transport import open_transport_session, run_interactive_command, run_rsync_command
from .ui import console, pick_with_fzf
from .workflow import run_copy, run_debug_session


configure(
    ClypiConfig(
        theme=Theme(
            usage=Styler(fg="cyan"),
            section_title=Styler(fg="magenta", bold=True),
            subcommand=Styler(fg="cyan", bold=True),
            long_option=Styler(fg="green", bold=True),
            short_option=Styler(fg="yellow", bold=True),
            positional=Styler(fg="cyan", bold=True),
            placeholder=Styler(fg="blue"),
            type_str=Styler(fg="yellow", bold=True),
            prompts=Styler(fg="green", bold=True),
        ),
        help_on_fail=True,
        fallback_term_width=110,
    )
)


def format_target_uri(target: TargetRef) -> str:
    if target.subpath == Path("."):
        return f"{target.kind}://{target.resource_name}"
    return f"{target.kind}://{target.resource_name}/{target.subpath.as_posix()}"


def format_mount_destination(target: TargetRef) -> str:
    if target.subpath == Path("."):
        return settings.helper_mount_path
    return f"{settings.helper_mount_path}/{target.subpath.as_posix()}"


def print_mount_info(pod_name: str, target: TargetRef) -> None:
    console.print(
        Panel.fit(
            f"Pod: {pod_name}\nTarget: {format_target_uri(target)}\nMounted at: {settings.helper_mount_path}",
            title="Mount Info",
            border_style="cyan",
        )
    )


def print_copy_info(source_dir: Path, pod_name: str, target: TargetRef) -> None:
    console.print(
        Panel.fit(
            "\n".join(
                [
                    f"Source: {source_dir}",
                    f"Target: {format_target_uri(target)}",
                    f"Pod: {pod_name}",
                    f"Mounted at: {settings.helper_mount_path}",
                    f"Destination in pod: {format_mount_destination(target)}",
                ]
            ),
            title="Copy Info",
            border_style="cyan",
        )
    )


def build_copy_request(
    source_dir: Path,
    raw_target: str,
    context_name: str | None,
    namespace: str | None,
    uid: int | None,
    gid: int | None,
    keep_pod: bool,
    port_forward_mode: PortForwardMode,
) -> CopyRequest:
    return CopyRequest(
        source_dir=source_dir,
        context_name=context_name,
        namespace=namespace,
        target=parse_target(raw_target),
        uid=uid,
        gid=gid,
        keep_pod=keep_pod,
        port_forward_mode=port_forward_mode,
    )


def build_debug_request(
    raw_target: str,
    context_name: str | None,
    namespace: str | None,
    keep_pod: bool,
    shell: str,
) -> DebugRequest:
    return DebugRequest(
        context_name=context_name,
        namespace=namespace,
        target=parse_target(raw_target),
        keep_pod=keep_pod,
        shell=shell,
    )


def resolve_target(
    client: KubeClient | object,
    namespace: str,
    target: TargetRef,
    interactive: bool,
    picker: Callable[[list[str], str, bool], list[str]] = pick_with_fzf,
) -> TargetRef:
    if target.resource_name:
        return target

    if not interactive:
        raise ValueError("Target PVC name is missing and interactive selection is disabled")

    pvc_names = client.list_pvcs(namespace)  # type: ignore[attr-defined]
    if not pvc_names:
        raise ValueError(f"No PVCs found in namespace {namespace}")

    selected = picker(pvc_names, prompt="pvc> ", multi=False)
    if not selected:
        raise ValueError("No PVC selected")

    return TargetRef(kind=target.kind, resource_name=selected[0], subpath=target.subpath)


class Copy(Command):
    """Copy a local directory into a Kubernetes PVC target."""

    source_dir: Path = arg(help="Local source directory to copy")
    target: str = arg(help="Typed target URI, e.g. pvc://media/uploads")
    context: str | None = arg(default=None, help="Kubernetes context name")
    namespace: str | None = arg(default=None, help="Kubernetes namespace")
    uid: int | None = arg(default=None, help="Override destination UID")
    gid: int | None = arg(default=None, help="Override destination GID")
    no_fzf: bool = arg(default=False, help="Disable interactive PVC selection")
    keep_pod: bool = arg(default=False, help="Keep helper pod after copy")
    port_forward_mode: PortForwardMode = arg(
        default="auto",
        help="Port forward mode: auto, python, or kubectl",
    )

    async def run(self) -> None:
        request = build_copy_request(
            source_dir=self.source_dir,
            raw_target=self.target,
            context_name=self.context,
            namespace=self.namespace,
            uid=self.uid,
            gid=self.gid,
            keep_pod=self.keep_pod,
            port_forward_mode=self.port_forward_mode,
        )
        if not request.source_dir.is_dir():
            raise ValueError(f"Source directory does not exist: {request.source_dir}")

        kube = KubeClient(context_name=request.context_name)
        namespace = request.namespace or kube.current_namespace()
        if not namespace:
            raise ValueError("Namespace is required. Pass --namespace or configure one in kubeconfig.")

        request = CopyRequest(
            source_dir=request.source_dir,
            context_name=request.context_name,
            namespace=namespace,
            target=resolve_target(
                client=kube,
                namespace=namespace,
                target=request.target,
                interactive=not self.no_fzf,
            ),
            uid=request.uid,
            gid=request.gid,
            keep_pod=request.keep_pod,
            port_forward_mode=request.port_forward_mode,
        )
        async with Spinner(
            title=f"Copying into {format_target_uri(request.target)}",
            animation=Spin.DOTS,
            prefix=" ",
            suffix="...",
            speed=1.2,
        ):
            session = run_copy(
                request=request,
                kube=kube,
                helper_image=settings.helper_image,
                helper_mount_path=settings.helper_mount_path,
                rsync_port=settings.helper_rsync_port,
                pod_name_suffix=None,
                on_pod_ready=lambda pod_name: print_copy_info(request.source_dir, pod_name, request.target),
                open_transport=lambda mode, namespace, pod_name, remote_port: open_transport_session(
                    mode=mode,
                    core_api=kube._core_api,
                    kubectl_bin=settings.kubectl_bin,
                    namespace=namespace,
                    pod_name=pod_name,
                    remote_port=remote_port,
                ),
                run_rsync=run_rsync_command,
                rsync_bin=settings.rsync_bin,
            )
        console.print(
            "[green]"
            f"Copied {request.source_dir} to {format_target_uri(request.target)} "
            f"via {session.transport} port-forward on localhost:{session.local_port}. "
            f"Mounted at {settings.helper_mount_path} in pod {session.pod_name}"
            "[/green]"
        )


class Debug(Command):
    """Attach an interactive shell to a helper pod with a mounted PVC target."""

    target: str = arg(help="Typed target URI, e.g. pvc://media/debug")
    context: str | None = arg(default=None, help="Kubernetes context name")
    namespace: str | None = arg(default=None, help="Kubernetes namespace")
    no_fzf: bool = arg(default=False, help="Disable interactive PVC selection")
    keep_pod: bool = arg(default=False, help="Keep helper pod after the shell exits")
    shell: str = arg(default="sh", help="Shell to execute inside the helper pod")

    async def run(self) -> None:
        request = build_debug_request(
            raw_target=self.target,
            context_name=self.context,
            namespace=self.namespace,
            keep_pod=self.keep_pod,
            shell=self.shell,
        )

        kube = KubeClient(context_name=request.context_name)
        namespace = request.namespace or kube.current_namespace()
        if not namespace:
            raise ValueError("Namespace is required. Pass --namespace or configure one in kubeconfig.")

        request = DebugRequest(
            context_name=request.context_name,
            namespace=namespace,
            target=resolve_target(
                client=kube,
                namespace=namespace,
                target=request.target,
                interactive=not self.no_fzf,
            ),
            keep_pod=request.keep_pod,
            shell=request.shell,
        )

        async with Spinner(
            title=f"Attaching debug shell to {format_target_uri(request.target)}",
            animation=Spin.DOTS,
            prefix=" ",
            suffix="...",
            speed=1.2,
        ):
            session = run_debug_session(
                request=request,
                kube=kube,
                helper_image=settings.helper_image,
                helper_mount_path=settings.helper_mount_path,
                kubectl_bin=settings.kubectl_bin,
                pod_name_suffix=None,
                on_pod_ready=lambda pod_name: print_mount_info(pod_name, request.target),
                attach_shell=run_interactive_command,
            )
        console.print(
            "[green]"
            f"Debug shell finished for {format_target_uri(request.target)} "
            f"on pod {session.pod_name} mounted at {settings.helper_mount_path}"
            "[/green]"
        )


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in {"copy", "debug"}:
        command_name = sys.argv[1]
        raw_args = sys.argv[2:]
        if command_name == "copy" and raw_args and not raw_args[0].startswith("-"):
            if len(raw_args) < 2 or raw_args[1].startswith("-"):
                raise SystemExit("copy expects SOURCE_DIR and TARGET")
            raw_args = ["--source-dir", raw_args[0], "--target", raw_args[1], *raw_args[2:]]
        if command_name == "debug" and raw_args and not raw_args[0].startswith("-"):
            raw_args = ["--target", raw_args[0], *raw_args[1:]]
        parser = Copy if command_name == "copy" else Debug
        command = parser.parse(raw_args)
        error = command.start()
        if error:
            raise SystemExit(str(error))
        return

    console.print(f"[red]Unknown command. Try: {Path(sys.argv[0]).name} copy --help or {Path(sys.argv[0]).name} debug --help[/red]")
    raise SystemExit(2)
