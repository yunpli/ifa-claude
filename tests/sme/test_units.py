from ifa.families.sme.data.units import UNIT_REGISTRY, registry_by_target, to_yuan


def test_to_yuan_rounds_decimal_values():
    assert to_yuan("1.2345", 10_000) == 12345
    assert to_yuan("1.23456", 10_000) == 12346
    assert to_yuan(None, 10_000) is None


def test_unit_registry_has_core_moneyflow_targets():
    targets = registry_by_target()
    for name in [
        "buy_sm_amount_yuan",
        "sell_sm_amount_yuan",
        "buy_elg_amount_yuan",
        "sell_elg_amount_yuan",
        "net_mf_amount_yuan",
        "amount_yuan",
        "total_mv_yuan",
        "circ_mv_yuan",
    ]:
        assert name in targets
    assert len(UNIT_REGISTRY) >= 12
