import textwrap

import pytest

from stockscout.config import Config, Secrets, load_config, load_secrets


def test_load_config_reads_yaml(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        textwrap.dedent(
            """
            universe:
              markets: [KR]
              exclude_financials: true
            lookback:
              primary_window: 3
            signal:
              min_score: 65
              drop_vs_avg_threshold: -0.35
            """
        ),
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.universe.markets == ["KR"]
    assert cfg.lookback.primary_window == 3
    assert cfg.signal.min_score == 65
    assert cfg.signal.drop_vs_avg_threshold == -0.35


def test_load_config_applies_defaults_for_omitted_sections(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("universe:\n  markets: [KR]\n", encoding="utf-8")

    cfg = load_config(path)

    assert cfg.hard_filters.max_debt_ratio == 200.0
    assert cfg.lookback.price_years == [1, 3, 5]
    assert cfg.sources.kr_price == "pykrx"


def test_primary_window_must_be_in_price_years(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        "lookback:\n  price_years: [1, 3]\n  primary_window: 5\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="primary_window"):
        load_config(path)


def test_load_secrets_requires_dart_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("SEC_USER_AGENT=x\n", encoding="utf-8")

    with pytest.raises(ValueError, match="DART_API_KEY"):
        load_secrets(env)


def test_load_secrets_reads_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("DART_API_KEY=abc123\n", encoding="utf-8")

    secrets = load_secrets(env)

    assert secrets.dart_api_key == "abc123"
