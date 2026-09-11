import textwrap
from pathlib import Path

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


# --- 실제 설정 파일 회귀 테스트 ---------------------------------------
#
# 위 테스트들은 전부 임시 YAML을 만들어 쓴다. 정작 시스템이 실제로 읽는
# config/settings.yaml은 아무도 검증하지 않아, 손으로 고치다 깨뜨리면
# 런타임에서야 발견된다. 아래 테스트들이 그 공백을 메운다.

REAL_SETTINGS = Path(__file__).parent.parent / "config" / "settings.yaml"


def test_real_settings_file_loads():
    cfg = load_config(REAL_SETTINGS)

    assert set(cfg.scoring) == {"profitability", "growth", "stability", "cashflow"}
    assert cfg.signal.min_score == 70
    assert cfg.lookback.primary_window in cfg.lookback.price_years


def test_real_settings_scoring_sums_to_100():
    """4개 축 × 25점 = 100점.

    배점을 손보다 총점이 틀어지면 점수가 조용히 왜곡된다. 여기서 잡는다.
    """
    cfg = load_config(REAL_SETTINGS)

    per_axis = {
        axis: sum(m.points for m in metrics.values())
        for axis, metrics in cfg.scoring.items()
    }

    assert per_axis == {
        "profitability": 25,
        "growth": 25,
        "stability": 25,
        "cashflow": 25,
    }
    assert sum(per_axis.values()) == 100


def test_scoring_bands_parse_as_numeric_pairs():
    cfg = load_config(REAL_SETTINGS)

    roe = cfg.scoring["profitability"]["roe_avg3"]

    assert roe.points == 10
    assert roe.bands[0] == (0.15, 10.0)
    assert roe.lower_is_better is False


def test_scoring_metric_without_bands_defaults_to_empty():
    cfg = load_config(REAL_SETTINGS)

    assert cfg.scoring["profitability"]["op_margin_trend"].bands == []


def test_lower_is_better_round_trips():
    cfg = load_config(REAL_SETTINGS)

    assert cfg.scoring["stability"]["debt_ratio"].lower_is_better is True


# --- 비밀값 우선순위 --------------------------------------------------


def test_env_var_overrides_dotenv_file(tmp_path, monkeypatch):
    """load_secrets의 docstring이 약속하는 동작.

    뒤집히면 잘못된 키로 API를 호출하게 되는데, 기존 테스트는 이 경로를
    한 번도 지나지 않았다.
    """
    env = tmp_path / ".env"
    env.write_text(
        "DART_API_KEY=from-file\nSEC_USER_AGENT=agent-from-file\n", encoding="utf-8"
    )
    monkeypatch.setenv("DART_API_KEY", "from-environ")

    secrets = load_secrets(env)

    assert secrets.dart_api_key == "from-environ"
    assert secrets.sec_user_agent == "agent-from-file"


def test_blank_env_var_does_not_override_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DART_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("DART_API_KEY", "")

    assert load_secrets(env).dart_api_key == "from-file"
