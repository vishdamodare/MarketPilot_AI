from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost/marketpilot"
    secret_key: str = "dev-only-change-me-please-32-bytes-min!"
    cookie_secure: bool = False  # set True behind HTTPS
    token_ttl_minutes: int = 60 * 12

    # PayPal: secrets live only in backend env. Sandbox by default.
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str = ""
    paypal_partner_attribution_id: str = ""  # BN code, only for approved partners
    paypal_partner_id: str = ""  # platform's own merchant/payer id (multiparty)

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    sync_interval_seconds: int = 900  # background sync cadence; 0 disables the in-process scheduler (use cron: python -m app.jobs)
    smtp_host: str = ""  # empty -> notifications stored in DB only

    @property
    def paypal_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def paypal_is_sandbox(self) -> bool:
        return "sandbox" in self.paypal_base_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
