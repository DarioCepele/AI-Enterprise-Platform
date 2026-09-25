import pytest
from pydantic import ValidationError

from demo.config import get_settings


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_CHAT_COMPLETION_MODEL", "m")
    monkeypatch.setenv("DEMO_FAKE_CLIENT", "true")

    s = get_settings()

    assert s.base_url == "http://localhost:1234/v1"
    assert s.api_key == "k"
    assert s.model == "m"
    assert s.use_fake_client is True


def test_fake_client_defaults_to_false(monkeypatch):
    monkeypatch.delenv("DEMO_FAKE_CLIENT", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")

    assert get_settings().use_fake_client is False


def test_allowed_origins_default_covers_next_fallback_port(monkeypatch):
    """Next slides to 3001 when 3000 is taken: both must pass CORS."""
    monkeypatch.delenv("DEMO_ALLOWED_ORIGINS", raising=False)

    origins = get_settings().allowed_origins

    assert "http://localhost:3000" in origins
    assert "http://localhost:3001" in origins


def test_allowed_origins_read_from_env(monkeypatch):
    monkeypatch.setenv("DEMO_ALLOWED_ORIGINS", "http://a.test , http://b.test")

    assert get_settings().allowed_origins == ("http://a.test", "http://b.test")


def test_the_product_has_a_name_and_a_language(monkeypatch):
    monkeypatch.delenv("DEMO_PRODUCT_NAME", raising=False)
    monkeypatch.delenv("DEMO_PRODUCT_LANGUAGE", raising=False)

    settings = get_settings()

    assert settings.product_name
    assert settings.product_language


def test_the_name_and_the_language_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("DEMO_PRODUCT_NAME", "Acme Copilot")
    monkeypatch.setenv("DEMO_PRODUCT_LANGUAGE", "English")

    settings = get_settings()

    assert settings.product_name == "Acme Copilot"
    assert settings.product_language == "English"


def test_the_default_scope_is_configurable(monkeypatch):
    monkeypatch.setenv("DEMO_DEFAULT_SCOPE", "acme-tenant")

    assert get_settings().default_scope == "acme-tenant"


def test_a_missing_api_key_is_named_before_the_first_turn(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEMO_FAKE_CLIENT", "false")

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        get_settings().require_model_access()


def test_the_fake_client_needs_no_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEMO_FAKE_CLIENT", "true")

    get_settings().require_model_access()


def test_a_number_that_is_not_a_number_is_refused_by_name(monkeypatch):
    monkeypatch.setenv("DEMO_SUBAGENT_WAIT_SECONDS", "presto")

    with pytest.raises(ValidationError, match="DEMO_SUBAGENT_WAIT_SECONDS"):
        get_settings()


def test_variables_that_are_not_ours_are_ignored(monkeypatch):
    monkeypatch.setenv("SOMETHING_ELSE", "whatever")

    assert get_settings().product_name


def test_the_instructions_carry_the_name_and_the_language():
    from demo.agents.master import instructions_for

    prompt = instructions_for("Acme Copilot", "English")

    assert "Acme Copilot" in prompt
    assert "Answer in English" in prompt
    assert "{product}" not in prompt
