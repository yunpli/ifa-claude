"""Tests for the three classify_* helpers — edge cases that have caused bugs
historically (None propagation, exact-boundary values, infinity handling)."""
from __future__ import annotations

import math
from decimal import Decimal

import pytest

from ifa.families.research.analyzer.factors import (
    FactorStatus,
    classify_higher_better,
    classify_in_band,
    classify_lower_better,
)


class TestClassifyHigherBetter:
    def test_none_returns_unknown(self):
        assert classify_higher_better(None, healthy_min=10, warning_below=10, critical_below=5) \
            == FactorStatus.UNKNOWN

    def test_above_warning_is_green(self):
        assert classify_higher_better(15, healthy_min=10, warning_below=10, critical_below=5) \
            == FactorStatus.GREEN

    def test_exact_warning_boundary_is_green(self):
        # Boundary at warning_below: rule is "< warning → yellow", so equal → green
        assert classify_higher_better(10, healthy_min=10, warning_below=10, critical_below=5) \
            == FactorStatus.GREEN

    def test_below_warning_is_yellow(self):
        assert classify_higher_better(8, healthy_min=10, warning_below=10, critical_below=5) \
            == FactorStatus.YELLOW

    def test_below_critical_is_red(self):
        assert classify_higher_better(2, healthy_min=10, warning_below=10, critical_below=5) \
            == FactorStatus.RED

    def test_decimal_input_works(self):
        # Common in our codebase — values arrive as Decimal from Postgres
        assert classify_higher_better(Decimal("4.73"), healthy_min=10, warning_below=10,
                                       critical_below=5) == FactorStatus.RED

    def test_negative_values(self):
        assert classify_higher_better(-5, healthy_min=0, warning_below=0, critical_below=-10) \
            == FactorStatus.YELLOW
        assert classify_higher_better(-15, healthy_min=0, warning_below=0, critical_below=-10) \
            == FactorStatus.RED


class TestClassifyLowerBetter:
    def test_none_returns_unknown(self):
        assert classify_lower_better(None, warning_above=60, critical_above=75) \
            == FactorStatus.UNKNOWN

    def test_below_warning_is_green(self):
        assert classify_lower_better(40, warning_above=60, critical_above=75) \
            == FactorStatus.GREEN

    def test_exact_warning_boundary_is_green(self):
        # Rule: "> warning → yellow", so equal → green
        assert classify_lower_better(60, warning_above=60, critical_above=75) \
            == FactorStatus.GREEN

    def test_above_warning_is_yellow(self):
        assert classify_lower_better(70, warning_above=60, critical_above=75) \
            == FactorStatus.YELLOW

    def test_above_critical_is_red(self):
        assert classify_lower_better(80, warning_above=60, critical_above=75) \
            == FactorStatus.RED


class TestClassifyInBand:
    def test_none_returns_unknown(self):
        assert classify_in_band(None, healthy_low=0.8, healthy_high=1.2) \
            == FactorStatus.UNKNOWN

    def test_inside_band_is_green(self):
        assert classify_in_band(1.0, healthy_low=0.8, healthy_high=1.2) \
            == FactorStatus.GREEN

    def test_at_band_edge_is_green(self):
        assert classify_in_band(0.8, healthy_low=0.8, healthy_high=1.2) \
            == FactorStatus.GREEN
        assert classify_in_band(1.2, healthy_low=0.8, healthy_high=1.2) \
            == FactorStatus.GREEN

    def test_just_outside_band_is_yellow(self):
        # default warning_band=0.2, span=0.4 → yellow zone is [0.72, 1.28]
        assert classify_in_band(0.75, healthy_low=0.8, healthy_high=1.2) \
            == FactorStatus.YELLOW
        assert classify_in_band(1.25, healthy_low=0.8, healthy_high=1.2) \
            == FactorStatus.YELLOW

    def test_far_outside_band_is_red(self):
        assert classify_in_band(0.5, healthy_low=0.8, healthy_high=1.2) \
            == FactorStatus.RED
        assert classify_in_band(1.5, healthy_low=0.8, healthy_high=1.2) \
            == FactorStatus.RED


class TestNonFiniteHandling:
    """NaN / Inf / strings must never silently classify as GREEN.

    Naive `<` against NaN returns False in Python, so without the explicit
    coerce-to-finite step a NaN factor would become GREEN. Defense-in-depth:
    cache._sanitize blocks NaN at ingest, classify_* blocks at compute time.
    """

    def test_nan_higher_better_is_unknown(self):
        assert classify_higher_better(math.nan, healthy_min=10, warning_below=10,
                                       critical_below=5) == FactorStatus.UNKNOWN

    def test_nan_lower_better_is_unknown(self):
        assert classify_lower_better(math.nan, warning_above=60, critical_above=75) \
            == FactorStatus.UNKNOWN

    def test_nan_in_band_is_unknown(self):
        assert classify_in_band(math.nan, healthy_low=0.8, healthy_high=1.2) \
            == FactorStatus.UNKNOWN

    def test_positive_inf_is_unknown(self):
        assert classify_higher_better(math.inf, healthy_min=10, warning_below=10,
                                       critical_below=5) == FactorStatus.UNKNOWN

    def test_negative_inf_is_unknown(self):
        assert classify_higher_better(-math.inf, healthy_min=10, warning_below=10,
                                       critical_below=5) == FactorStatus.UNKNOWN

    def test_unparseable_string_is_unknown(self):
        assert classify_higher_better("not a number", healthy_min=10,  # type: ignore
                                       warning_below=10, critical_below=5) \
            == FactorStatus.UNKNOWN
