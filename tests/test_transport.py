from kopy.transport import (
    PortForwardError,
    build_rsync_command,
    open_port_forward,
)


def test_build_rsync_command_upload() -> None:
    command = build_rsync_command(
        source="/tmp/source/",
        dest="rsync://127.0.0.1:1873/volume/uploads/",
        rsync_bin="rsync",
    )

    assert command == [
        "rsync",
        "-a",
        "--delete",
        "--numeric-ids",
        "/tmp/source/",
        "rsync://127.0.0.1:1873/volume/uploads/",
    ]


def test_build_rsync_command_download() -> None:
    command = build_rsync_command(
        source="rsync://127.0.0.1:1873/volume/uploads/",
        dest="/tmp/dest/",
        rsync_bin="rsync",
    )

    assert command == [
        "rsync",
        "-a",
        "--delete",
        "--numeric-ids",
        "rsync://127.0.0.1:1873/volume/uploads/",
        "/tmp/dest/",
    ]


def test_open_port_forward_falls_back_to_kubectl_in_auto_mode() -> None:
    events: list[str] = []

    def python_launcher() -> str:
        events.append("python")
        raise PortForwardError("python failed")

    def kubectl_launcher() -> str:
        events.append("kubectl")
        return "kubectl-session"

    session = open_port_forward(
        mode="auto",
        python_launcher=python_launcher,
        kubectl_launcher=kubectl_launcher,
    )

    assert session == ("kubectl", "kubectl-session")
    assert events == ["python", "kubectl"]


def test_open_port_forward_raises_when_python_only_mode_fails() -> None:
    def python_launcher() -> str:
        raise PortForwardError("python failed")

    try:
        open_port_forward(
            mode="python",
            python_launcher=python_launcher,
            kubectl_launcher=lambda: "kubectl-session",
        )
    except PortForwardError as exc:
        assert "python failed" in str(exc)
    else:
        raise AssertionError("expected python mode to fail")
