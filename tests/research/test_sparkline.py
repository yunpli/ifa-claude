"""Tests for the inline SVG sparkline generator."""
from __future__ import annotations

import re

from ifa.families.research.report.sparkline import render_sparkline


class TestRenderSparkline:
    def test_too_few_points_returns_empty(self):
        assert render_sparkline([]) == ""
        assert render_sparkline([1.0]) == ""
        assert render_sparkline([1.0, None]) == ""

    def test_basic_uptrend_renders_svg(self):
        out = render_sparkline([1, 2, 3, 4, 5])
        assert out.startswith("<svg")
        assert out.endswith("</svg>")
        # Path command should have M (moveto) followed by L (lineto)s
        assert "M" in out
        assert "L" in out

    def test_uptrend_uses_green_color(self):
        out = render_sparkline([1, 2, 3, 4, 5])
        # _COLOR_UP = "#2e7d32"
        assert "#2e7d32" in out

    def test_downtrend_uses_red_color(self):
        out = render_sparkline([5, 4, 3, 2, 1])
        # _COLOR_DOWN = "#c62828"
        assert "#c62828" in out

    def test_flat_uses_gray_color(self):
        # ±2% considered flat
        out = render_sparkline([100, 101, 100, 99, 100])
        # _COLOR_FLAT = "#888"
        assert "#888" in out

    def test_none_breaks_path_into_segments(self):
        # A None mid-series should produce a new "M" command (path break)
        out = render_sparkline([1, 2, None, 4, 5])
        # Count M's — should be at least 2 (segment before and after the None)
        m_count = len(re.findall(r"\bM\d", out))
        assert m_count >= 2

    def test_last_point_dot_present(self):
        out = render_sparkline([1, 2, 3, 4, 5])
        # Final-value dot is a <circle> element
        assert "<circle" in out

    def test_custom_dimensions_respected(self):
        out = render_sparkline([1, 2, 3, 4], width=200, height=40)
        assert 'width="200"' in out
        assert 'height="40"' in out
        assert 'viewBox="0 0 200 40"' in out

    def test_constant_series_does_not_crash(self):
        # Span = 0 must not divide-by-zero
        out = render_sparkline([5, 5, 5, 5, 5])
        assert out.startswith("<svg")
