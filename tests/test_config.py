import textwrap
from pathlib import Path

import pytest

from pydantic import ValidationError

from stockscout.config import (
    Config,
    HardFilterConfig,
    Secrets,
    load_config,
    load_secrets,
)


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


# --- 저장 가능한 창 ---------------------------------------------------


def test_unsupported_price_window_is_rejected(tmp_path):
    """스키마에 없는 창은 설정 단계에서 막는다.

    analysis_results에는 avg_price_1y/3y/5y 컬럼만 있다. 2년 창을
    허용하면 계산은 되지만 저장 시 조용히 버려지고, 출력 단계에서
    drop_vs_avg_2y를 읽으며 잡히지 않는 KeyError로 analyze 명령
    전체가 죽는다. 실제로 재현됐던 경로다.
    """
    path = tmp_path / "settings.yaml"
    path.write_text(
        "lookback:\n  price_years: [1, 2, 3, 5]\n  primary_window: 2\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="저장할 수 없는 창"):
        load_config(path)


def test_error_names_the_offending_window_and_the_allowed_set(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        "lookback:\n  price_years: [1, 3, 4, 5]\n  primary_window: 5\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc:
        load_config(path)

    message = str(exc.value)
    assert "[4]" in message
    assert "[1, 3, 5]" in message


def test_subset_of_supported_windows_is_allowed(tmp_path):
    """전부 쓰지 않아도 된다. 1년과 3년만으로도 유효하다."""
    path = tmp_path / "settings.yaml"
    path.write_text(
        "lookback:\n  price_years: [1, 3]\n  primary_window: 3\n", encoding="utf-8"
    )

    cfg = load_config(path)

    assert cfg.lookback.price_years == [1, 3]


def test_hard_filter_year_counts_must_be_at_least_one():
    """연수 0을 설정 단계에서 막는다.

    interest_coverage_years=0이면 이자보상배율 필터가 정확히 뒤집힌다.
    coverages가 빈 목록이 될 수 있고, 그때 len([]) == 0이 참이면서
    all([])도 True라 통과 조건이 곧 실패 조건이 된다 — 이자비용을
    측정할 수 없는 회사가 "이자 미충당"으로 제외된다. 실측 196행 중
    157행이 대체값이므로 유니버스 대부분이 여기 해당한다.

    나머지 둘은 지금 같은 역전을 일으키지 않지만, 개수로 창을 자르는
    같은 모양의 필드이므로 함께 막는다. 셋 중 하나만 닫아 두면 다음에
    누가 필터를 고칠 때 어느 것이 안전한지 매번 다시 따져야 한다.
    """
    for field in (
        "consecutive_operating_loss_years",
        "interest_coverage_years",
        "min_history_years",
    ):
        with pytest.raises(ValidationError):
            HardFilterConfig(**{field: 0})


def test_hard_filter_year_counts_reject_negatives():
    with pytest.raises(ValidationError):
        HardFilterConfig(interest_coverage_years=-1)


def test_hard_filter_year_count_of_one_is_allowed():
    """1은 막지 않는다. 막는 것은 0 이하뿐이다."""
    config = HardFilterConfig(interest_coverage_years=1)

    assert config.interest_coverage_years == 1


def test_hard_filter_year_counts_are_rejected_through_yaml(tmp_path):
    """설정 파일을 거쳐 들어와도 막힌다 — 실제로 값이 오는 경로다."""
    path = tmp_path / "settings.yaml"
    path.write_text(
        "hard_filters:\n  interest_coverage_years: 0\n", encoding="utf-8"
    )

    with pytest.raises(ValidationError):
        load_config(path)


# --- 시장별 비밀값 (계획 E) -------------------------------------------


def test_us_does_not_require_a_dart_key(tmp_path, monkeypatch):
    """미국만 수집하는데 DART 키가 없다고 죽으면 안 된다."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    env = tmp_path / ".env"
    env.write_text("SEC_USER_AGENT=stockscout me@example.com\n", encoding="utf-8")

    secrets = load_secrets(env, market="US")

    assert secrets.sec_user_agent == "stockscout me@example.com"
    assert secrets.dart_api_key is None


def test_us_requires_a_contact_in_the_user_agent(tmp_path, monkeypatch):
    """SEC는 연락처 없는 User-Agent를 403으로 막는다 (설계서 §18.2)."""
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    env = tmp_path / ".env"
    env.write_text("SEC_USER_AGENT=stockscout/0.1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        load_secrets(env, market="US")


def test_us_requires_a_user_agent_at_all(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    env = tmp_path / ".env"
    env.write_text("DART_API_KEY=abc\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        load_secrets(env, market="US")


def test_kr_is_still_the_default_market(tmp_path, monkeypatch):
    """기존 호출(load_secrets(env))은 지금처럼 DART 키를 요구한다."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("SEC_USER_AGENT=stockscout me@example.com\n", encoding="utf-8")

    with pytest.raises(ValueError, match="DART_API_KEY"):
        load_secrets(env)


def test_unknown_market_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="XX"):
        load_secrets(tmp_path / ".env", market="XX")


# --- 시장별 하드필터 (계획 E 후속) ------------------------------------


def test_market_hard_filters_override_only_the_given_fields():
    from stockscout.config import Config

    config = Config.model_validate({
        "hard_filters": {"max_debt_ratio": 200, "min_history_years": 5},
        "hard_filters_by_market": {"US": {"max_debt_ratio": 400}},
    })

    us = config.hard_filters_for("US")
    assert us.max_debt_ratio == 400
    assert us.min_history_years == 5
    assert config.hard_filters_for("KR") == config.hard_filters


def test_a_market_override_is_validated_like_the_base():
    """덮어쓴 결과도 HardFilterConfig의 검증(ge=1 등)을 거친다."""
    from stockscout.config import Config

    config = Config.model_validate({
        "hard_filters_by_market": {"US": {"interest_coverage_years": 0}}})

    with pytest.raises(ValidationError):
        config.hard_filters_for("US")


def test_an_unknown_override_field_is_rejected():
    """오타(max_dept_ratio)가 조용히 무시되면 필터가 그대로 켜져 있다."""
    from stockscout.config import Config

    config = Config.model_validate({
        "hard_filters_by_market": {"US": {"max_dept_ratio": 400}}})

    with pytest.raises(ValueError, match="max_dept_ratio"):
        config.hard_filters_for("US")


def test_real_settings_turn_off_the_debt_filter_for_us_only():
    """설계서 §18.4: 자사주 매입으로 자본이 작은 미국 대형주는 부채비율이 무의미하다."""
    import math

    config = load_config()

    assert math.isinf(config.hard_filters_for("US").max_debt_ratio)
    assert config.hard_filters_for("KR").max_debt_ratio == 200
