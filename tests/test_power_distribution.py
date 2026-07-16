"""Tests for power_distribution.py's 2-battery selection/allocation logic.

Uses a lightweight duck-typed fake instead of a real BatteryAdapter — the
module only reads `.name`, `.max_charge_w`, `.max_discharge_w`, `.data`.
"""
from custom_components.anker_solix_energy_manager.power_distribution import PowerDistribution


class FakeBattery:
    def __init__(
        self,
        name,
        max_charge_w=3500,
        max_discharge_w=3500,
        soc=50.0,
        available=True,
        charge_limit_soc=100.0,
        discharge_limit_soc=0.0,
    ):
        self.name = name
        self.max_charge_w = max_charge_w
        self.max_discharge_w = max_discharge_w
        self.data = {
            "available": available,
            "battery_soc": soc,
            "charge_limit_soc": charge_limit_soc,
            "discharge_limit_soc": discharge_limit_soc,
        }


def test_available_batteries_excludes_unavailable():
    a = FakeBattery("A", available=True)
    b = FakeBattery("B", available=False)
    pd = PowerDistribution(batteries=[a, b])
    assert pd.available_batteries(True) == [a]


def test_available_batteries_excludes_full_battery_from_charging():
    a = FakeBattery("A", soc=100.0)
    b = FakeBattery("B", soc=50.0)
    pd = PowerDistribution(batteries=[a, b])
    assert a not in pd.available_batteries(is_charging=True)
    assert a in pd.available_batteries(is_charging=False)


def test_available_batteries_excludes_empty_battery_from_discharging():
    a = FakeBattery("A", soc=0.0)
    b = FakeBattery("B", soc=50.0)
    pd = PowerDistribution(batteries=[a, b])
    assert a not in pd.available_batteries(is_charging=False)
    assert a in pd.available_batteries(is_charging=True)


def test_available_batteries_respects_configured_charge_limit_soc():
    # Battery's own "don't charge above 80%" longevity setting must be
    # honored even though 80% is well below the physical 100% ceiling.
    a = FakeBattery("A", soc=85.0, charge_limit_soc=80.0)
    b = FakeBattery("B", soc=85.0, charge_limit_soc=100.0)
    pd = PowerDistribution(batteries=[a, b])
    available = pd.available_batteries(is_charging=True)
    assert a not in available
    assert b in available


def test_available_batteries_respects_configured_discharge_limit_soc():
    # Battery's own "don't discharge below 20%" longevity setting must be
    # honored even though 20% is well above the physical 0% floor.
    a = FakeBattery("A", soc=15.0, discharge_limit_soc=20.0)
    b = FakeBattery("B", soc=15.0, discharge_limit_soc=0.0)
    pd = PowerDistribution(batteries=[a, b])
    available = pd.available_batteries(is_charging=False)
    assert a not in available
    assert b in available


def test_available_batteries_charge_limit_soc_below_soc_still_allows_charging():
    a = FakeBattery("A", soc=50.0, charge_limit_soc=80.0)
    pd = PowerDistribution(batteries=[a])
    assert a in pd.available_batteries(is_charging=True)


def test_select_batteries_low_power_uses_single_unit():
    a = FakeBattery("A", max_discharge_w=3500, soc=80)
    b = FakeBattery("B", max_discharge_w=3500, soc=40)
    pd = PowerDistribution(batteries=[a, b])
    selected = pd.select_batteries(500, [a, b], is_charging=False)
    assert len(selected) == 1
    assert selected[0] is a  # highest SOC drained first


def test_select_batteries_high_power_splits_across_units():
    a = FakeBattery("A", max_discharge_w=3500, soc=80)
    b = FakeBattery("B", max_discharge_w=3500, soc=40)
    pd = PowerDistribution(batteries=[a, b])
    selected = pd.select_batteries(3000, [a, b], is_charging=False)
    assert len(selected) == 2


def test_select_batteries_zero_power_clears_selection():
    a = FakeBattery("A")
    b = FakeBattery("B")
    pd = PowerDistribution(batteries=[a, b])
    pd.select_batteries(3000, [a, b], is_charging=False)
    assert len(pd.active_discharge_batteries) == 2
    pd.select_batteries(0, [a, b], is_charging=False)
    assert pd.active_discharge_batteries == []


def test_distribute_power_proportional_to_limits():
    a = FakeBattery("A", max_charge_w=1000)
    b = FakeBattery("B", max_charge_w=3000)
    pd = PowerDistribution(batteries=[a, b])
    allocation = pd.distribute_power(400, [a, b], is_charging=True)
    # Proportional split: A gets 1/4, B gets 3/4 of 400W
    assert allocation[a] == 100
    assert allocation[b] == 300


def test_distribute_power_proportional_share_never_exceeds_own_limit():
    # With remaining_power pre-clamped to min(total, total_capacity), a
    # battery's proportional share can never exceed its own limit — see the
    # note in distribute_power's docstring. Verify that invariant directly
    # rather than asserting a specific capped value that can't occur here.
    a = FakeBattery("A", max_charge_w=500)
    b = FakeBattery("B", max_charge_w=3000)
    pd = PowerDistribution(batteries=[a, b])
    allocation = pd.distribute_power(3000, [a, b], is_charging=True)
    assert allocation[a] <= 500
    assert allocation[b] <= 3000
    assert allocation[a] + allocation[b] <= 3000


def test_distribute_power_uncapped_request_matches_total_capacity_at_the_limit():
    # Requesting exactly the combined capacity: every battery lands at (or
    # within rounding of) its own limit.
    a = FakeBattery("A", max_charge_w=500)
    b = FakeBattery("B", max_charge_w=3000)
    pd = PowerDistribution(batteries=[a, b])
    allocation = pd.distribute_power(3500, [a, b], is_charging=True)
    assert allocation[a] == 500
    assert allocation[b] == 3000


def test_distribute_power_never_exceeds_total_capacity():
    a = FakeBattery("A", max_charge_w=1000)
    b = FakeBattery("B", max_charge_w=1000)
    pd = PowerDistribution(batteries=[a, b])
    allocation = pd.distribute_power(5000, [a, b], is_charging=True)
    assert sum(allocation.values()) <= 2000


def test_distribute_power_empty_selection_returns_empty():
    pd = PowerDistribution(batteries=[])
    assert pd.distribute_power(1000, [], is_charging=True) == {}
