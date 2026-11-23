import pytest

from app.config import ConfigError, Settings, validate_settings


def _settings(**overrides):
    base = dict(ENVIRONMENT="development", DATABASE_URL="postgresql://u:p@localhost:5432/db")
    base.update(overrides)
    return Settings(**base)


def test_development_configuration_is_accepted():
    validate_settings(_settings())


def test_all_problems_are_reported_together():
    with pytest.raises(ConfigError) as exc:
        validate_settings(_settings(CLIP_COUNT=3, DATABASE_URL="mysql://nope",
                                    STORAGE_BACKEND="supabase", SUPABASE_URL="http://insecure"))
    problems = " | ".join(exc.value.problems)
    assert "CLIP_COUNT must be 5" in problems
    assert "DATABASE_URL must be a postgresql" in problems
    assert "SUPABASE_URL must be an https URL" in problems
    assert "SUPABASE_SERVICE_ROLE_KEY is required" in problems


def test_production_requires_secrets_and_redis():
    with pytest.raises(ConfigError) as exc:
        validate_settings(_settings(ENVIRONMENT="production", AUTH_JWT_SECRET="short", REDIS_URL="",
                                    GEMINI_API_KEY="", AI_PROVIDER="gemini"))
    problems = " | ".join(exc.value.problems)
    assert "AUTH_JWT_SECRET must be at least 32" in problems
    assert "REDIS_URL is required in production" in problems
    assert "GEMINI_API_KEY is required" in problems


def test_fake_ai_provider_is_refused_in_production():
    with pytest.raises(ConfigError) as exc:
        validate_settings(_settings(ENVIRONMENT="production", AUTH_JWT_SECRET="x" * 40,
                                    REDIS_URL="redis://r:6379/0", GEMINI_API_KEY="k", AI_PROVIDER="fake"))
    assert any("fake is not allowed in production" in p for p in exc.value.problems)


def test_invalid_environment_name_fails_fast():
    with pytest.raises(ValueError):
        Settings(ENVIRONMENT="staging")


def test_clip_duration_bounds_are_validated():
    with pytest.raises(ConfigError):
        validate_settings(_settings(CLIP_MIN_SECONDS=60, CLIP_MAX_SECONDS=30))
