"""Typed encrypted credentials for one ordered Claude account pool."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.modules.shared.schemas import DashboardModel

ANTHROPIC_KIND = "anthropic"
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"


class ClaudeAccountInput(DashboardModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    access_token: SecretStr
    refresh_token: SecretStr
    expires_at: datetime

    @field_validator("access_token", "refresh_token")
    @classmethod
    def nonempty_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Account tokens must not be empty")
        return value

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Account expiry must include a timezone")
        return value


class ClaudeAccountInfo(DashboardModel):
    id: str
    name: str
    expires_at: datetime
    cooldown_until: datetime | None = None
    needs_reauthentication: bool = False


class ClaudeAccount(BaseModel):
    id: str
    name: str
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    expires_at: datetime
    cooldown_until: datetime | None = None
    needs_reauthentication: bool = False
    last_error: Literal["auth", "rate_limit", "upstream", "refresh"] | None = None
    refresh_claim: str | None = None
    refresh_claim_until: datetime | None = None

    def public(self) -> ClaudeAccountInfo:
        return ClaudeAccountInfo(
            id=self.id,
            name=self.name,
            expires_at=self.expires_at,
            cooldown_until=self.cooldown_until,
            needs_reauthentication=self.needs_reauthentication,
        )


class ClaudePool(BaseModel):
    version: Literal[1] = 1
    accounts: list[ClaudeAccount] = Field(min_length=1, max_length=50)

    @classmethod
    def from_inputs(cls, accounts: list[ClaudeAccountInput]) -> ClaudePool:
        if len({account.id for account in accounts}) != len(accounts):
            raise ValueError("Claude account IDs must be unique")
        return cls(
            accounts=[
                ClaudeAccount(
                    id=account.id,
                    name=account.name,
                    access_token=account.access_token.get_secret_value(),
                    refresh_token=account.refresh_token.get_secret_value(),
                    expires_at=account.expires_at,
                )
                for account in accounts
            ]
        )
