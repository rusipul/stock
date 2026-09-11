import pytest

from stockscout.models import OPTIONAL_FIELDS, REQUIRED_FIELDS, Financials


def make(**overrides) -> Financials:
    base = dict(
        ticker="005930.KS", fiscal_year=2025, currency="KRW",
        revenue=300.0, operating_income=30.0, total_equity=200.0, total_liabilities=60.0,
        # 선택 필드도 기본값을 채워, 개별 테스트가 명시적으로 None을 준
        # 필드만 결측으로 잡히도록 한다 (그렇지 않으면 missing_fields가
        # 손대지 않은 다른 선택 필드까지 결측으로 잡아 격리되지 않는다).
        net_income=10.0, total_assets=100.0, current_assets=50.0,
        current_liabilities=20.0, interest_expense=1.0, operating_cash_flow=15.0,
        capex=5.0, shares_outstanding=1000.0, dividend_per_share=1.0,
    )
    base.update(overrides)
    return Financials(**base)


def test_required_and_optional_sets_do_not_overlap():
    assert REQUIRED_FIELDS & OPTIONAL_FIELDS == set()


def test_all_declared_fields_exist_on_model():
    fields = set(Financials.model_fields)
    assert REQUIRED_FIELDS <= fields
    assert OPTIONAL_FIELDS <= fields


def test_missing_fields_lists_absent_required_values():
    fin = make(revenue=None, operating_income=None)

    assert set(fin.missing_fields) == {"revenue", "operating_income"}


def test_missing_fields_lists_absent_optional_values():
    fin = make(capex=None, interest_expense=None, current_assets=1.0,
               current_liabilities=1.0, dividend_per_share=0.0,
               net_income=1.0, total_assets=1.0, operating_cash_flow=1.0,
               shares_outstanding=1.0)

    assert set(fin.missing_fields) == {"capex", "interest_expense"}


def test_has_required_is_false_when_any_required_missing():
    assert make().has_required is True
    assert make(total_equity=None).has_required is False


def test_debt_ratio_computed():
    fin = make(total_liabilities=100.0, total_equity=50.0)

    assert fin.debt_ratio == pytest.approx(200.0)


def test_debt_ratio_is_none_when_equity_nonpositive():
    assert make(total_equity=0.0).debt_ratio is None
    assert make(total_equity=-10.0).debt_ratio is None


def test_operating_margin_and_roe():
    fin = make(revenue=1000.0, operating_income=150.0, net_income=100.0, total_equity=500.0)

    assert fin.operating_margin == pytest.approx(0.15)
    assert fin.roe == pytest.approx(0.20)


def test_margin_is_none_when_revenue_nonpositive():
    assert make(revenue=0.0).operating_margin is None


def test_fcf_uses_capex_as_positive_magnitude():
    fin = make(operating_cash_flow=100.0, capex=30.0)

    assert fin.free_cash_flow == pytest.approx(70.0)


def test_fcf_is_none_without_capex():
    assert make(operating_cash_flow=100.0, capex=None).free_cash_flow is None
