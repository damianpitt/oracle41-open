"""Test command-line startup options.

The cases verify version output and guarded maintenance behavior without starting an interactive session.
They protect the packaged command entry point.
"""

import pytest

from oracle41_open.app.main import main


def test_version_command_exits_without_starting_gui(capsys: object) -> None:
    result = main(["--version"])

    assert result == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert output == "Oracle41 Open 0.4.0a11\n"


def test_live_validation_option_remains_disabled_without_opt_in(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORACLE41_RUN_LIVE_PROVIDER_VALIDATION", raising=False)

    result = main(["--validate-providers-live"])

    assert result == 2
    assert "disabled" in capsys.readouterr().out
