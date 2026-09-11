from pathlib import Path

import pytest

from stockscout.sources.dart import parse_corp_codes

FIXTURE = Path(__file__).parent / "fixtures" / "dart_corpcode_sample.xml"


def test_parse_maps_stock_code_to_corp_code():
    mapping = parse_corp_codes(FIXTURE.read_bytes())

    assert mapping["005930"] == "00126380"
    assert mapping["000660"] == "00164779"


def test_parse_excludes_unlisted_companies():
    mapping = parse_corp_codes(FIXTURE.read_bytes())

    assert len(mapping) == 2
    assert all(code.strip() for code in mapping)


def test_parse_raises_on_garbage():
    with pytest.raises(ValueError, match="CORPCODE"):
        parse_corp_codes(b"not xml at all")
