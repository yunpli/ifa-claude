import pytest

from ifa.families.sme.data.source_resolver import resolve_source


def test_resolve_core_smartmoney_sources():
    assert resolve_source("moneyflow").fqtn == "smartmoney.raw_moneyflow"
    assert resolve_source("daily").fqtn == "smartmoney.raw_daily"
    assert resolve_source("sw_member").fqtn == "smartmoney.raw_sw_member"


def test_rejects_unknown_source():
    with pytest.raises(KeyError):
        resolve_source("not_a_source")


def test_mvp_rejects_non_smartmoney_source_mode():
    with pytest.raises(ValueError):
        resolve_source("moneyflow", source_mode="sme_only")
