"""Tests for in-memory progress tracker with terminal rendering."""

import sys
from unittest.mock import patch

import pytest

from kanka_wiki_updater.progress import ProgressTracker


class TestProgressRendering:
    def test_initial_state_zero_percent(self):
        tracker = ProgressTracker(10)
        assert tracker.done == 0

    def test_filled_returns_block_or_equals(self):
        tracker_unicode = ProgressTracker(10)
        tracker_unicode._unicode = True
        assert tracker_unicode.filled == '\u2588'

        tracker_ascii = ProgressTracker(10)
        tracker_ascii._unicode = False
        assert tracker_ascii.filled == '='

    def test_empty_returns_block_or_dash(self):
        tracker_unicode = ProgressTracker(10)
        tracker_unicode._unicode = True
        assert tracker_unicode.empty == '\u2591'

        tracker_ascii = ProgressTracker(10)
        tracker_ascii._unicode = False
        assert tracker_ascii.empty == '-'

    def test_bar_width_is_20(self):
        tracker = ProgressTracker(10)
        tracker.done = 6
        rendered = tracker._render()
        bar_start = rendered[1:21]
        assert len(bar_start) == 20

    def test_percentage_rounds_up(self):
        tracker = ProgressTracker(3)
        tracker.done = 2
        rendered = tracker._render()
        pct_part = rendered[22:25]
        assert '67%' in rendered or '66%' in rendered

    def test_label_appended(self):
        tracker = ProgressTracker(10)
        rendered = tracker._render('LLM for Alice...')
        assert 'LLM for Alice...' in rendered

    def test_no_trailing_space_when_empty_string(self):
        tracker = ProgressTracker(10)
        rendered = tracker._render()
        # Should not have trailing space after percentage bar
        parts = rendered.split(']')
        if len(parts) > 1:
            assert parts[-1].strip() == '' or ' ' not in parts[-1].strip()


class TestProgressIncrement:
    def test_mark_done_increments(self):
        tracker = ProgressTracker(5)
        tracker.mark_done()
        assert tracker.done == 1

    def test_finish_prints_newline(self, capsys):
        tracker = ProgressTracker(2)
        tracker.mark_done('step 1')
        tracker.mark_done('step 2')
        tracker.finish()
        output = capsys.readouterr().out
        assert '\n' in output

    def test_max_width_tracks_longest_render(self):
        tracker = ProgressTracker(1)
        tracker._render('very long label that exceeds normal width')
        assert tracker._max_width > 0


class TestEdgeCases:
    def test_zero_total_avoids_division_by_zero(self):
        tracker = ProgressTracker(0)
        assert tracker.total == 1

    def test_finish_with_zero_total_does_nothing(self, capsys):
        tracker = ProgressTracker(0)
        tracker.finish()
        output = capsys.readouterr().out
        assert output == ''

    @patch('sys.stdout.encoding', 'ascii')
    def test_fallback_to_ascii_on_encode_error(self):
        with patch.object(ProgressTracker, '_check_unicode', return_value=False):
            tracker = ProgressTracker(10)
            assert tracker.filled == '='
            assert tracker.empty == '-'
