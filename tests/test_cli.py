import socket

import pytest

from jetkvm_cli.cli import build_parser, main


def test_parser_builds_and_parses_subcommands():
    p = build_parser()
    a = p.parse_args(["--port", "5000", "keys", "ctrl+alt+f2", "enter"])
    assert a.port == 5000 and a.combos == ["ctrl+alt+f2", "enter"]
    a = p.parse_args(["click", "10", "20", "--button", "right", "--double"])
    assert (a.x, a.y, a.button, a.double) == (10.0, 20.0, "right", True)


def test_helpful_error_when_daemon_not_running():
    with socket.socket() as s:  # find a port nothing is listening on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(SystemExit, match="jetkvm serve"):
        main(["--port", str(port), "status"])
