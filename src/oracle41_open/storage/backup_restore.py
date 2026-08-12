"""Export and restore local Oracle41 Open state.

Backups contain validated settings and a consistent SQLite snapshot inside a versioned zip bundle.
Provider keys, private endpoint URLs, and disposable cache data are excluded.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase
from oracle41_open.storage.settings import AppSettings, SettingsStore

_BACKUP_FORMAT = "oracle41-open-backup"
_BACKUP_VERSION = 1
_MANIFEST_FILENAME = "manifest.json"
_SETTINGS_FILENAME = "settings.json"
_SQLITE_FILENAME = "state.sqlite3"
_REQUIRED_SQLITE_TABLES = {
    "schema_meta",
    "watchlist_entries",
    "wallet_notes",
    "wallet_note_tags",
    "saved_views",
    "snapshots",
}


class BackupRestoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupMetadata:
    created_at: datetime
    format_version: int
    backup_path: Path


class BackupRestoreService:
    def __init__(self, settings_store: SettingsStore, sqlite_database: SQLiteDatabase) -> None:
        self._settings_store = settings_store
        self._sqlite_database = sqlite_database

    def export_backup(self, output_path: Path) -> BackupMetadata:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(tz=UTC)

        with tempfile.TemporaryDirectory(prefix="oracle41-backup-") as temp_dir:
            temp_root = Path(temp_dir)
            settings_path = temp_root / _SETTINGS_FILENAME
            sqlite_snapshot_path = temp_root / _SQLITE_FILENAME
            manifest_path = temp_root / _MANIFEST_FILENAME

            settings = self._settings_store.load()
            settings_payload = settings.model_dump(mode="json")
            settings_path.write_bytes(json_dumps(settings_payload, pretty=True))

            self._create_sqlite_snapshot(sqlite_snapshot_path)

            manifest_payload = {
                "format": _BACKUP_FORMAT,
                "version": _BACKUP_VERSION,
                "created_at": created_at.isoformat(),
                "files": {
                    "settings": _SETTINGS_FILENAME,
                    "sqlite": _SQLITE_FILENAME,
                },
            }
            manifest_path.write_bytes(json_dumps(manifest_payload, pretty=True))

            with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(manifest_path, arcname=_MANIFEST_FILENAME)
                bundle.write(settings_path, arcname=_SETTINGS_FILENAME)
                bundle.write(sqlite_snapshot_path, arcname=_SQLITE_FILENAME)

        return BackupMetadata(
            created_at=created_at,
            format_version=_BACKUP_VERSION,
            backup_path=output_path,
        )

    def restore_backup(self, input_path: Path) -> AppSettings:
        if not input_path.exists() or not input_path.is_file():
            raise BackupRestoreError(f"Backup file not found: {input_path}")

        with tempfile.TemporaryDirectory(prefix="oracle41-restore-") as temp_dir:
            temp_root = Path(temp_dir)
            extracted_sqlite_path = temp_root / _SQLITE_FILENAME

            try:
                with zipfile.ZipFile(input_path, mode="r") as bundle:
                    file_names = set(bundle.namelist())
                    if _MANIFEST_FILENAME not in file_names:
                        raise BackupRestoreError("Backup bundle is missing manifest.json.")

                    manifest = json_loads(bundle.read(_MANIFEST_FILENAME))
                    settings_member, sqlite_member = _manifest_members(manifest)

                    if settings_member not in file_names:
                        raise BackupRestoreError("Backup bundle is missing settings payload.")
                    if sqlite_member not in file_names:
                        raise BackupRestoreError("Backup bundle is missing SQLite payload.")

                    settings_payload = json_loads(bundle.read(settings_member))
                    try:
                        restored_settings = AppSettings.model_validate(settings_payload)
                    except PydanticValidationError as error:
                        raise BackupRestoreError(
                            f"Backup settings payload is invalid: {error}"
                        ) from error

                    extracted_sqlite_path.write_bytes(bundle.read(sqlite_member))
            except zipfile.BadZipFile as error:
                raise BackupRestoreError("Backup file is not a valid zip archive.") from error

            self._validate_sqlite_snapshot(extracted_sqlite_path)
            self._replace_sqlite_database(extracted_sqlite_path)
            self._settings_store.save(restored_settings)
            self._sqlite_database.initialize()
            return restored_settings

    def _create_sqlite_snapshot(self, output_path: Path) -> None:
        self._sqlite_database.initialize()
        source_path = self._sqlite_database.file_path
        if not source_path.exists():
            raise BackupRestoreError("SQLite database file is unavailable for backup export.")

        source_conn = sqlite3.connect(source_path)
        try:
            target_conn = sqlite3.connect(output_path)
            try:
                source_conn.backup(target_conn)
            finally:
                target_conn.close()
        finally:
            source_conn.close()

    def _validate_sqlite_snapshot(self, sqlite_path: Path) -> None:
        try:
            conn = sqlite3.connect(sqlite_path)
        except sqlite3.Error as error:
            raise BackupRestoreError("Backup SQLite payload could not be opened.") from error
        try:
            integrity_row = conn.execute("PRAGMA integrity_check;").fetchone()
            integrity_ok = (
                isinstance(integrity_row, tuple)
                and len(integrity_row) > 0
                and isinstance(integrity_row[0], str)
                and integrity_row[0].lower() == "ok"
            )
            if not integrity_ok:
                raise BackupRestoreError("Backup SQLite payload failed integrity check.")

            table_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            table_names = {row[0] for row in table_rows if isinstance(row[0], str)}
            missing_tables = sorted(_REQUIRED_SQLITE_TABLES - table_names)
            if missing_tables:
                missing = ", ".join(missing_tables)
                raise BackupRestoreError(
                    f"Backup SQLite payload is missing required tables: {missing}"
                )
        except sqlite3.Error as error:
            raise BackupRestoreError("Backup SQLite payload could not be validated.") from error
        finally:
            conn.close()

    def _replace_sqlite_database(self, source_path: Path) -> None:
        target_path = self._sqlite_database.file_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_target = target_path.with_name(f"{target_path.name}.restore_tmp")
        shutil.copy2(source_path, temporary_target)
        os.replace(temporary_target, target_path)


def _manifest_members(manifest: object) -> tuple[str, str]:
    if not isinstance(manifest, dict):
        raise BackupRestoreError("Backup manifest payload is invalid.")

    raw_format = manifest.get("format")
    if raw_format != _BACKUP_FORMAT:
        raise BackupRestoreError("Backup manifest format is not supported.")

    raw_version = manifest.get("version")
    if not isinstance(raw_version, int) or raw_version < 1 or raw_version > _BACKUP_VERSION:
        raise BackupRestoreError("Backup manifest version is not supported.")

    raw_files = manifest.get("files")
    if not isinstance(raw_files, dict):
        raise BackupRestoreError("Backup manifest files payload is invalid.")

    settings_member = raw_files.get("settings")
    sqlite_member = raw_files.get("sqlite")
    if not isinstance(settings_member, str) or not settings_member.strip():
        raise BackupRestoreError("Backup manifest settings file is invalid.")
    if not isinstance(sqlite_member, str) or not sqlite_member.strip():
        raise BackupRestoreError("Backup manifest sqlite file is invalid.")
    return settings_member, sqlite_member
