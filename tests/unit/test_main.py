"""Test command-line startup options.

The cases verify version output and smoke-test behavior without starting an interactive session.
They protect the packaged command entry point.
"""

from oracle41_open.app.main import main


def test_version_command_exits_without_starting_gui(capsys: object) -> None:
    result = main(["--version"])

    assert result == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert output == "Oracle41 Open 0.4.0a1\n"
