from ifa.families.sme.params.store import load_market_structure_params


def test_market_structure_params_loads_continuous_search_space():
    params = load_market_structure_params(profile="baseline")
    meta = params["_meta"]
    assert meta["profile"] == "baseline"
    assert "search_space" in meta
    continuous = meta["search_space"]["continuous"]
    assert "primary.min_main_net_ratio" in continuous
    assert len(continuous["primary.min_main_net_ratio"]) == 2


def test_candidate_profile_is_distinct_from_baseline():
    baseline = load_market_structure_params(profile="baseline")
    candidate = load_market_structure_params(profile="mvp1_ytd_candidate")
    assert baseline["_meta"]["hash"] != candidate["_meta"]["hash"]
    assert candidate["primary"]["mode"] == "broad_positive_flow"
