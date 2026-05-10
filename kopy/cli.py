from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from clypi import ClypiConfig, Command, Spin, Spinner, Styler, Theme, arg, configure
from rich.panel import Panel

from .k8s import KubeClient
from .models import CopyRequest, DebugRequest, Endpoint, PortForwardMode, TakeoverRequest
from .settings import settings
from .target import parse_endpoint
from .transport import open_transport_session, run_interactive_command, run_rsync_command
from .ui import console, pick_with_fzf
from .workflow import run_copy, run_debug_session, run_takeover


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


def format_endpoint(endpoint: Endpoint) -> str:
    if endpoint.kind == "local":
        return str(endpoint.path)
    if endpoint.path == Path("."):
        return f"pvc://{endpoint.resource_name}"
    return f"pvc://{endpoint.resource_name}/{endpoint.path.as_posix()}"


def format_mount_destination(target: Endpoint) -> str:
    if target.path == Path("."):
        return settings.helper_mount_path
    return f"{settings.helper_mount_path}/{target.path.as_posix()}"


def print_mount_info(pod_name: str, target: Endpoint) -> None:
    console.print(
        Panel.fit(
            f"Pod: {pod_name}\nTarget: {format_endpoint(target)}\nMounted at: {settings.helper_mount_path}",
            title="Mount Info",
            border_style="cyan",
        )
    )


def print_copy_info(source: Endpoint, pod_name: str, target: Endpoint) -> None:
    console.print(
        Panel.fit(
            "\n".join(
                [
                    f"Source: {format_endpoint(source)}",
                    f"Target: {format_endpoint(target)}",
                    f"Pod: {pod_name}",
                    f"Mounted at: {settings.helper_mount_path}",
                ]
                + (
                    [f"Destination in pod: {format_mount_destination(target)}"]
                    if target.kind == "pvc"
                    else []
                )
            ),
            title="Copy Info",
            border_style="cyan",
        )
    )


def print_top_level_help() -> None:
    console.print("kopy")
    console.print("")
    console.print("Copy data between local paths and Kubernetes PVCs, and between Kubernetes PVCs.")
    console.print("")
    console.print("[cyan]Usage:[/cyan] [bold]kopy[/bold] <command> [options]")
    console.print("")
    console.print("[magenta bold]Commands[/magenta bold]")
    console.print("  [cyan bold]copy[/cyan bold]          Copy data between local paths and Kubernetes PVCs, and between Kubernetes PVCs")
    console.print("  [cyan bold]debug[/cyan bold]         Attach a shell to a helper pod with a mounted PVC")
    console.print("  [cyan bold]takeover-pvc[/cyan bold]  Rebind a migrated PVC volume to the original PVC name")
    console.print("")
    console.print("Run `kopy <command> --help` for command-specific options.")


def build_copy_request(
    raw_source: str,
    raw_target: str,
    context_name: str | None,
    namespace: str | None,
    uid: int | None,
    gid: int | None,
    keep_pod: bool,
    port_forward_mode: PortForwardMode,
    create_pvc: bool,
    storage_class: str | None,
) -> CopyRequest:
    return CopyRequest(
        source=parse_endpoint(raw_source),
        target=parse_endpoint(raw_target),
        context_name=context_name,
        namespace=namespace,
        uid=uid,
        gid=gid,
        keep_pod=keep_pod,
        port_forward_mode=port_forward_mode,
        create_pvc=create_pvc,
        storage_class=storage_class,
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
        target=parse_endpoint(raw_target),
        keep_pod=keep_pod,
        shell=shell,
    )


def build_takeover_request(
    raw_source: str,
    raw_target: str,
    context_name: str | None,
    namespace: str | None,
    set_retain: bool,
) -> TakeoverRequest:
    return TakeoverRequest(
        source=parse_endpoint(raw_source),
        target=parse_endpoint(raw_target),
        context_name=context_name,
        namespace=namespace,
        set_retain=set_retain,
    )


def resolve_endpoint(
    client: KubeClient | object,
    namespace: str,
    endpoint: Endpoint,
    interactive: bool,
    picker: Callable[[list[str], str, bool], list[str]] = pick_with_fzf,
) -> Endpoint:
    if endpoint.kind != "pvc" or endpoint.resource_name:
        return endpoint

    if not interactive:
        raise ValueError("PVC name is missing and interactive selection is disabled")

    pvc_names = client.list_pvcs(namespace)  # type: ignore[attr-defined]
    if not pvc_names:
        raise ValueError(f"No PVCs found in namespace {namespace}")

    selected = picker(pvc_names, prompt="pvc> ", multi=False)
    if not selected:
        raise ValueError("No PVC selected")

    return Endpoint(kind="pvc", resource_name=selected[0], path=endpoint.path)


class Copy(Command):
    """Copy data between local paths and Kubernetes PVCs, and between Kubernetes PVCs."""

    source: str = arg(help="Source: local path or pvc://name/subpath")
    target: str = arg(help="Target: local path or pvc://name/subpath")
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
    create_pvc: bool = arg(default=False, help="Create the target PVC if it does not exist")
    storage_class: str | None = arg(default=None, help="StorageClass for a newly created target PVC")

    async def run(self) -> None:
        request = build_copy_request(
            raw_source=self.source,
            raw_target=self.target,
            context_name=self.context,
            namespace=self.namespace,
            uid=self.uid,
            gid=self.gid,
            keep_pod=self.keep_pod,
            port_forward_mode=self.port_forward_mode,
            create_pvc=self.create_pvc,
            storage_class=self.storage_class,
        )
        if request.source.kind == "local" and not request.source.path.is_dir():
            raise ValueError(f"Source directory does not exist: {request.source.path}")

        kube = KubeClient(context_name=request.context_name)
        namespace = request.namespace or kube.current_namespace()
        if not namespace:
            raise ValueError("Namespace is required. Pass --namespace or configure one in kubeconfig.")

        request = CopyRequest(
            source=resolve_endpoint(
                client=kube,
                namespace=namespace,
                endpoint=request.source,
                interactive=not self.no_fzf,
            ),
            target=resolve_endpoint(
                client=kube,
                namespace=namespace,
                endpoint=request.target,
                interactive=not self.no_fzf,
            ),
            context_name=request.context_name,
            namespace=namespace,
            uid=request.uid,
            gid=request.gid,
            keep_pod=request.keep_pod,
            port_forward_mode=request.port_forward_mode,
            create_pvc=request.create_pvc,
            storage_class=request.storage_class,
        )
        async with Spinner(
            title=f"Copying {format_endpoint(request.source)} → {format_endpoint(request.target)}",
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
                on_pod_ready=lambda pod_name: print_copy_info(request.source, pod_name, request.target),
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
        port_info = f" via {session.transport} port-forward on localhost:{session.local_port}" if session.local_port else ""
        console.print(
            "[green]"
            f"Copied {format_endpoint(request.source)} to {format_endpoint(request.target)}"
            f"{port_info} in pod {session.pod_name}"
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
            target=resolve_endpoint(
                client=kube,
                namespace=namespace,
                endpoint=request.target,
                interactive=not self.no_fzf,
            ),
            keep_pod=request.keep_pod,
            shell=request.shell,
        )

        async with Spinner(
            title=f"Attaching debug shell to {format_endpoint(request.target)}",
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
            f"Debug shell finished for {format_endpoint(request.target)} "
            f"on pod {session.pod_name} mounted at {settings.helper_mount_path}"
            "[/green]"
        )


class TakeoverPvc(Command):
    """Rebind a migrated PVC volume to the original PVC name."""

    source: str = arg(help="Migrated PVC root, e.g. pvc://media-migrated")
    target: str = arg(help="Original PVC root name to take over, e.g. pvc://media")
    context: str | None = arg(default=None, help="Kubernetes context name")
    namespace: str | None = arg(default=None, help="Kubernetes namespace")
    set_retain: bool = arg(default=False, help="Temporarily set PV reclaim policy to Retain during takeover")

    async def run(self) -> None:
        request = build_takeover_request(
            raw_source=self.source,
            raw_target=self.target,
            context_name=self.context,
            namespace=self.namespace,
            set_retain=self.set_retain,
        )

        kube = KubeClient(context_name=request.context_name)
        namespace = request.namespace or kube.current_namespace()
        if not namespace:
            raise ValueError("Namespace is required. Pass --namespace or configure one in kubeconfig.")

        request = TakeoverRequest(
            source=request.source,
            target=request.target,
            context_name=request.context_name,
            namespace=namespace,
            set_retain=request.set_retain,
        )
        async with Spinner(
            title=f"Taking over {format_endpoint(request.target)} with {format_endpoint(request.source)}",
            animation=Spin.DOTS,
            prefix=" ",
            suffix="...",
            speed=1.2,
        ):
            session = run_takeover(request=request, kube=kube)
        console.print(
            "[green]"
            f"PVC takeover complete: {format_endpoint(request.target)} now points to PV {session.pv_name}"
            "[/green]"
        )


def main() -> None:
    try:
        if len(sys.argv) <= 1 or sys.argv[1] in {"-h", "--help", "help"}:
            print_top_level_help()
            return
        if len(sys.argv) > 1 and sys.argv[1] == "debug":
            raw_args = sys.argv[2:]
            if raw_args and not raw_args[0].startswith("-"):
                raw_args = ["--target", raw_args[0], *raw_args[1:]]
            command = Debug.parse(raw_args)
        elif len(sys.argv) > 1 and sys.argv[1] == "takeover-pvc":
            raw_args = sys.argv[2:]
            if len(raw_args) >= 2 and not raw_args[0].startswith("-") and not raw_args[1].startswith("-"):
                raw_args = ["--source", raw_args[0], "--target", raw_args[1], *raw_args[2:]]
            command = TakeoverPvc.parse(raw_args)
        else:
            raw_args = sys.argv[1:]
            # positional shorthand: kopy ./src pvc://dest  →  --source ./src --target pvc://dest
            if len(raw_args) >= 2 and not raw_args[0].startswith("-") and not raw_args[1].startswith("-"):
                raw_args = ["--source", raw_args[0], "--target", raw_args[1], *raw_args[2:]]
            command = Copy.parse(raw_args)
        error = command.start()
        if error:
            raise SystemExit(str(error))
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise SystemExit(1)
