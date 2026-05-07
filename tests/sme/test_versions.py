from ifa.families.sme.versions import logic_versions


def test_logic_versions_cover_mvp1_feature_families():
    versions = logic_versions()
    assert versions["schema"].startswith("sme_mvp1_schema_")
    for key in ["stock_orderflow", "sector_orderflow", "diffusion", "state", "labels", "market_structure", "strategy_eval"]:
        assert key in versions
        assert versions[key]
