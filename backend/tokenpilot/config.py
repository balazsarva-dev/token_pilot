import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str
    # Admin keys for API sync (optional). repr=False keeps them out of logs and tracebacks.
    openai_admin_key: str | None = field(default=None, repr=False)
    anthropic_admin_key: str | None = field(default=None, repr=False)


def load_settings() -> Settings:
    """Read settings from the environment. Fails fast if something required is missing."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set (see backend/.env.example)")
    return Settings(
        database_url=database_url,
        openai_admin_key=os.environ.get("OPENAI_ADMIN_KEY", "").strip() or None,
        anthropic_admin_key=os.environ.get("ANTHROPIC_ADMIN_KEY", "").strip() or None,
    )
