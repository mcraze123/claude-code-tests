from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # eBay API (developer.ebay.com -> your application keyset)
    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_marketplace_id: str = "EBAY_US"
    ebay_env: str = "production"  # or "sandbox"

    # Comp price provider: "manual" (default) or "marketplace_insights" (requires eBay Limited Release approval)
    comp_provider: str = "manual"

    # Database
    database_path: str = "/app/data/ebay_deals.db"

    # SMTP (credentials only - schedule/recipient/enabled live in the DB, editable via Settings UI)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from_address: str = ""

    # Web server
    web_host: str = "0.0.0.0"
    web_port: int = 8000

    # Timezone used for schedule display/interpretation
    app_timezone: str = "America/New_York"


settings = Settings()
