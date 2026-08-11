from oracle41_open.app.main import main


def test_version_command_exits_without_starting_gui(capsys: object) -> None:
    result = main(["--version"])

    assert result == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert output == "Oracle41 Open 0.3.0a3\n"
