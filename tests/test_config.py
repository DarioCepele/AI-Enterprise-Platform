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
    """Next slitta su 3001 se la 3000 e' occupata: entrambe devono passare il CORS."""
    monkeypatch.delenv("DEMO_ALLOWED_ORIGINS", raising=False)

    origins = get_settings().allowed_origins

    assert "http://localhost:3000" in origins
    assert "http://localhost:3001" in origins


def test_allowed_origins_read_from_env(monkeypatch):
    monkeypatch.setenv("DEMO_ALLOWED_ORIGINS", "http://a.test , http://b.test")

    assert get_settings().allowed_origins == ("http://a.test", "http://b.test")
