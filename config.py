"""
Application configuration and settings management.
Uses environment variables with sensible defaults.
"""

import os
from typing import List, Optional
from functools import lru_cache


class Settings:
    """
    Application settings loaded from environment variables.
    All settings have sensible defaults for development.
    """

    def __init__(self):
        # Application
        self.APP_NAME: str = os.getenv("APP_NAME", "F1 Points Calculator")
        self.APP_VERSION: str = os.getenv("APP_VERSION", "1.0.0")
        self.ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
        self.DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
        
        # Server
        self.HOST: str = os.getenv("HOST", "0.0.0.0")
        self.PORT: int = int(os.getenv("PORT", "8000"))
        
        # Database
        self.DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///cache.db")
        
        # Redis Cache
        self.REDIS_URL: Optional[str] = os.getenv("REDIS_URL")
        self.CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
        
        # Rate Limiting
        self.ENABLE_RATE_LIMITING: bool = os.getenv("ENABLE_RATE_LIMITING", "true").lower() == "true"
        self.RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
        self.RATE_LIMIT_PER_HOUR: int = int(os.getenv("RATE_LIMIT_PER_HOUR", "1000"))
        self.RATE_LIMIT_BURST: int = int(os.getenv("RATE_LIMIT_BURST", "10"))
        
        # CORS
        cors_origins = os.getenv("CORS_ORIGINS", "*")
        self.CORS_ORIGINS: List[str] = [origin.strip() for origin in cors_origins.split(",")]
        
        # AI Model Server (Ollama)
        self.OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        
        # Logging
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
        self.LOG_FORMAT: str = os.getenv(
            "LOG_FORMAT",
            "%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s"
        )
        
        # Security
        self.SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
        self.API_KEY_HEADER: str = os.getenv("API_KEY_HEADER", "X-API-Key")
        
        # Data Files
        self.DATA_DIR: str = os.getenv("DATA_DIR", ".")
        
        # Export/Upload
        self.EXPORT_DIR: str = os.getenv("EXPORT_DIR", "exports")
        self.MAX_EXPORT_SIZE_MB: int = int(os.getenv("MAX_EXPORT_SIZE_MB", "50"))

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.ENVIRONMENT.lower() in ["production", "prod"]

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.ENVIRONMENT.lower() in ["development", "dev"]

    def get_database_url(self) -> str:
        """Get the database URL with driver adjustments."""
        url = self.DATABASE_URL
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql+psycopg2://', 1)
        return url


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    Uses lru_cache to ensure settings are loaded only once.
    """
    return Settings()


# ============================================================================
# Environment Variable Documentation
# ============================================================================

ENV_DOCS = """
# F1 Points Calculator - Environment Variables

## Application
- APP_NAME: Application name (default: "F1 Points Calculator")
- APP_VERSION: Application version (default: "1.0.0")
- ENVIRONMENT: Environment name - development, staging, production (default: "development")
- DEBUG: Enable debug mode (default: "false")

## Server
- HOST: Server host (default: "0.0.0.0")
- PORT: Server port (default: "8000")

## Database
- DATABASE_URL: Database connection string (default: "sqlite:///cache.db")
  Examples:
  - SQLite: sqlite:///cache.db
  - PostgreSQL: postgresql://user:password@localhost:5432/dbname
  - PostgreSQL (Supabase): postgresql://user:password@host:5432/postgres

## Redis Cache (Optional)
- REDIS_URL: Redis connection string (default: None)
  Example: redis://localhost:6379/0
- CACHE_TTL_SECONDS: Cache time-to-live in seconds (default: "3600")

## Rate Limiting
- ENABLE_RATE_LIMITING: Enable rate limiting (default: "true")
- RATE_LIMIT_PER_MINUTE: Max requests per minute per client (default: "60")
- RATE_LIMIT_PER_HOUR: Max requests per hour per client (default: "1000")
- RATE_LIMIT_BURST: Max burst requests per second (default: "10")

## CORS
- CORS_ORIGINS: Comma-separated list of allowed origins (default: "*")
  Example: "http://localhost:3000,https://myapp.com"

## AI Model Server (Ollama)
- OLLAMA_BASE_URL: Ollama server URL (default: "http://localhost:11434")
- Fixed model: llama3.1:8b (selected in application code)

## Logging
- LOG_LEVEL: Logging level - DEBUG, INFO, WARNING, ERROR (default: "INFO")
- LOG_FORMAT: Log message format (default: includes timestamp, level, request_id)

## Security
- SECRET_KEY: Secret key for signing (default: "dev-secret-key-change-in-production")
  IMPORTANT: Change this in production!
- API_KEY_HEADER: Header name for API key authentication (default: "X-API-Key")

## Data
- DATA_DIR: Directory containing CSV data files (default: ".")
- EXPORT_DIR: Directory for exported files (default: "exports")
- MAX_EXPORT_SIZE_MB: Maximum export file size in MB (default: "50")
"""


def print_env_docs():
    """Print environment variable documentation."""
    print(ENV_DOCS)


if __name__ == "__main__":
    # Print settings documentation when run directly
    print_env_docs()
    
    # Print current settings
    print("\n" + "="*50)
    print("Current Settings:")
    print("="*50)
    settings = get_settings()
    for key, value in settings.__dict__.items():
        if "KEY" in key or "SECRET" in key:
            value = "***" if value else None
        print(f"  {key}: {value}")
