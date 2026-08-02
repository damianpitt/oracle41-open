from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from platformdirs import user_config_dir
from pydantic import BaseModel, Field, field_validator

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import Chain


class AppSettings(BaseModel):
    selected_chain: Chain = Chain.ETHEREUM
    hide_unverified: bool = True
    hide_dust: bool = False
    dust_threshold_usd: str = "1"
    wallet_overview_max_token_pages: int = Field(default=20, ge=1, le=200)
    wallet_overview_cache_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
    activity_cache_ttl_seconds: int = Field(default=120, ge=0, le=86_400)
    token_detail_cache_ttl_seconds: int = Field(default=120, ge=0, le=86_400)
    pricing_max_stale_age_seconds: int = Field(default=86_400, ge=0, le=604_800)
    cache_max_size_mb: int = Field(default=150, ge=10, le=500)

    @field_validator("dust_threshold_usd")
    @classmethod
    def _normalize_dust_threshold(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            return "0"
        try:
            parsed = Decimal(trimmed)
        except InvalidOperation as error:
            raise ValueError("Dust threshold must be numeric.") from error
        if parsed < 0:
            raise ValueError("Dust threshold cannot be negative.")
        normalized = parsed.normalize()
        if normalized == normalized.to_integral():
            return str(normalized.quantize(Decimal("1")))
        return format(normalized, "f")


@dataclass
class SettingsStore:
    file_path: Path

    @staticmethod
    def default() -> SettingsStore:
        root = Path(user_config_dir(appname="oracle41-open", appauthor=False))
        return SettingsStore(file_path=root / "settings.json")

    def load(self) -> AppSettings:
        if not self.file_path.exists():
            return AppSettings()
        data = json_loads(self.file_path.read_bytes())
        return AppSettings.model_validate(data)

    def save(self, settings: AppSettings) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = settings.model_dump(mode="json")
        self.file_path.write_bytes(json_dumps(payload, pretty=True))

    def update(self, **changes: object) -> AppSettings:
        current = self.load()
        updated = current.model_copy(update=changes)
        self.save(updated)
        return updated
