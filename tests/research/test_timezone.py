"""Tests for the always-Beijing-time convention.

These guard against regressions where someone reintroduces `date.today()` or
`datetime.now()` without an explicit BJT tz. The system targets China A-share
markets — all dates / display must be BJT regardless of host timezone.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from ifa.core.report.timezones import BJT, bjt_now, fmt_bjt, to_bjt


class TestBjtHelpers:
    def test_bjt_offset_is_eight_hours(self):
        assert BJT.utcoffset(None) == dt.timedelta(hours=8)

    def test_bjt_now_is_tz_aware(self):
        now = bjt_now()
        assert now.tzinfo is not None
        assert now.tzinfo.utcoffset(None) == dt.timedelta(hours=8)

    def test_to_bjt_converts_utc(self):
        # 2026-04-29 00:00 UTC == 2026-04-29 08:00 BJT (same date)
        utc = dt.datetime(2026, 4, 29, 0, 0, tzinfo=dt.timezone.utc)
        bjt = to_bjt(utc)
        assert bjt.year == 2026 and bjt.month == 4 and bjt.day == 29
        assert bjt.hour == 8

    def test_to_bjt_treats_naive_as_utc(self):
        naive = dt.datetime(2026, 4, 29, 0, 0)
        bjt = to_bjt(naive)
        assert bjt.day == 29
        assert bjt.hour == 8

    def test_to_bjt_handles_none(self):
        assert to_bjt(None) is None

    def test_to_bjt_rolls_date_correctly_near_midnight(self):
        # 2026-04-28 17:00 UTC == 2026-04-29 01:00 BJT (date rolls forward)
        utc = dt.datetime(2026, 4, 28, 17, 0, tzinfo=dt.timezone.utc)
        bjt = to_bjt(utc)
        assert bjt.day == 29  # rolled forward
        assert bjt.hour == 1


class TestPtMachineGetsBjtBusinessDate:
    """Simulate running on a PT host (UTC-7) to confirm bjt_now().date()
    returns the correct Beijing date, not the local PT date.
    """

    def test_pt_evening_yields_next_bjt_day(self):
        # 2026-04-28 22:00 PT = 2026-04-29 05:00 UTC = 2026-04-29 13:00 BJT
        pt = dt.timezone(dt.timedelta(hours=-7))
        fake_now = dt.datetime(2026, 4, 28, 22, 0, tzinfo=pt)
        with patch("ifa.core.report.timezones.dt") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now.astimezone(BJT)
            mock_dt.timezone = dt.timezone
            mock_dt.timedelta = dt.timedelta
            result = bjt_now()
        assert result.day == 29   # BJT date, not PT's 28
        assert result.hour == 13


class TestFmtBjt:
    def test_default_format(self):
        utc = dt.datetime(2026, 4, 29, 0, 0, tzinfo=dt.timezone.utc)
        s = fmt_bjt(utc)
        assert "2026-04-29 08:00" == s

    def test_custom_format(self):
        utc = dt.datetime(2026, 4, 29, 0, 0, tzinfo=dt.timezone.utc)
        s = fmt_bjt(utc, "%Y%m%d")
        assert s == "20260429"

    def test_none_returns_dash(self):
        assert fmt_bjt(None) == "—"


class TestParseAnnDate:
    """Tushare ann_date strings are BJT — _parse_anndate must tag with BJT."""

    def test_yyyymmdd_string_tagged_bjt(self):
        from ifa.families.research.jobs.company_events import _parse_anndate
        parsed = _parse_anndate("20260429")
        assert parsed is not None
        assert parsed.tzinfo is not None
        # offset = +08:00
        assert parsed.utcoffset() == dt.timedelta(hours=8)
        assert parsed.year == 2026 and parsed.month == 4 and parsed.day == 29

    def test_dashed_date_tagged_bjt(self):
        from ifa.families.research.jobs.company_events import _parse_anndate
        parsed = _parse_anndate("2026-04-29")
        assert parsed is not None
        assert parsed.utcoffset() == dt.timedelta(hours=8)
        assert parsed.day == 29
