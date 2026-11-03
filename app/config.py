"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the local POS foundation."""

    shopify_base_url: str = "http://localhost:8787/admin/api/2026-10"
    smt_base_url: str = "http://localhost:8787/smt"
    terminal_base_url: str = "http://localhost:8787/terminal"
    database_path: str = "eluno_pos.sqlite3"
    http_timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            shopify_base_url=os.getenv("ELUNO_SHOPIFY_BASE_URL", cls.shopify_base_url).rstrip("/"),
            smt_base_url=os.getenv("ELUNO_SMT_BASE_URL", cls.smt_base_url).rstrip("/"),
            terminal_base_url=os.getenv("ELUNO_TERMINAL_BASE_URL", cls.terminal_base_url).rstrip("/"),
            database_path=os.getenv("ELUNO_DATABASE_PATH", cls.database_path),
            http_timeout_seconds=float(os.getenv("ELUNO_HTTP_TIMEOUT_SECONDS", str(cls.http_timeout_seconds))),
        )
