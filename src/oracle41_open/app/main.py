from __future__ import annotations

import sys

from oracle41_open import __version__
from oracle41_open.app.bootstrap import build_container


def main(arguments: list[str] | None = None) -> int:
    application_arguments = list(sys.argv if arguments is None else [sys.argv[0], *arguments])
    if "--version" in application_arguments:
        print(f"Oracle41 Open {__version__}")
        return 0

    smoke_test = "--smoke-test" in application_arguments
    application_arguments = [
        argument for argument in application_arguments if argument != "--smoke-test"
    ]
    try:
        from PySide6.QtWidgets import QApplication
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError(
            "PySide6 is not installed. Install project dependencies with `pip install -e \".[dev]\"`."
        ) from exc

    from oracle41_open.gui.main_window import MainWindow

    app = QApplication(application_arguments)
    container = build_container()
    window = MainWindow(container=container)
    window.show()
    if smoke_test:
        app.processEvents()
        window.close()
        app.processEvents()
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
