"""Central configuration, loaded from environment variables / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _optional_float(name: str, default: float | None) -> float | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return float(raw) if raw else None


@dataclass
class Settings:
    # --- Store ---
    store_name: str = field(default_factory=lambda: os.getenv("STORE_NAME", "Loom & Ladle"))

    # --- LLM provider ---
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic").strip().lower())
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "").strip())
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001").strip())
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", "").strip())
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip())
    temperature: float | None = field(default_factory=lambda: _optional_float("LLM_TEMPERATURE", 0.2))

    # Prices in USD per 1M tokens (used for cost tracking). Verify against the provider's current pricing page.
    anthropic_price_input: float = field(default_factory=lambda: _float("ANTHROPIC_PRICE_INPUT_PER_MTOK", 1.0))
    anthropic_price_output: float = field(default_factory=lambda: _float("ANTHROPIC_PRICE_OUTPUT_PER_MTOK", 5.0))
    openai_price_input: float = field(default_factory=lambda: _float("OPENAI_PRICE_INPUT_PER_MTOK", 0.40))
    openai_price_output: float = field(default_factory=lambda: _float("OPENAI_PRICE_OUTPUT_PER_MTOK", 1.60))

    # --- Agent behaviour ---
    max_tool_rounds: int = field(default_factory=lambda: _int("MAX_TOOL_ROUNDS", 6))
    history_limit: int = field(default_factory=lambda: _int("HISTORY_LIMIT", 40))

    # --- Business policy (enforced in code, not by the LLM) ---
    auto_refund_limit: float = field(default_factory=lambda: _float("AUTO_REFUND_LIMIT", 200.0))
    return_window_days: int = field(default_factory=lambda: _int("RETURN_WINDOW_DAYS", 30))

    # --- Storage ---
    db_path: Path = field(default_factory=lambda: ROOT_DIR / os.getenv("DB_PATH", "data/shop.db"))
    outputs_dir: Path = field(default_factory=lambda: ROOT_DIR / "outputs")
    knowledge_dir: Path = field(default_factory=lambda: ROOT_DIR / "knowledge_base")

    # --- WhatsApp (Twilio) ---
    twilio_auth_token: str = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN", "").strip())
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"))

    # --- Server ---
    host: str = field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("PORT", 8000))

    @property
    def eval_results_path(self) -> Path:
        return self.outputs_dir / "eval_results.json"


def get_settings() -> Settings:
    return Settings()
