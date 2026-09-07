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
