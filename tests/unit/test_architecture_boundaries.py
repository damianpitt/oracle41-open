from pathlib import Path


def test_core_and_provider_layers_do_not_import_qt() -> None:
    project_root = Path(__file__).resolve().parents[2]
    source_root = project_root / "src" / "oracle41_open"
    for folder_name in ("core", "providers"):
        folder = source_root / folder_name
        for module_path in folder.rglob("*.py"):
            text = module_path.read_text(encoding="utf-8")
            assert "PySide6" not in text, f"Qt import found in {module_path}"
