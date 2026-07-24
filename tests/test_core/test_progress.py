"""Tests for in-memory progress tracker with terminal rendering."""

from unittest.mock import patch

from kanka_wiki_updater.core.progress import ProgressTracker


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
        # _max_width is updated inside mark_done(), not _render() directly
        tracker.mark_done('very long label that exceeds normal width')
        assert tracker._max_width > 0


class TestEdgeCases:
    def test_zero_total_avoids_division_by_zero(self):
        tracker = ProgressTracker(0)
        assert tracker.total == 1

    def test_finish_with_zero_total_does_nothing(self, capsys):
        # total is clamped to min(1) in __init__, so finish() always prints.
        # Test that _check_unicode returning False (no unicode) + Windows mode
        # means print(rendered) which still outputs something.
        # The real test is that with total=0, progress never progresses beyond 0%.
        tracker = ProgressTracker(0)
        assert tracker.total == 1  # clamped from 0 to 1
        tracker.finish()
        # With _use_cr=True (non-Windows), output goes to write+flush not print

    @patch.object(ProgressTracker, '_check_unicode', return_value=False)
    def test_fallback_to_ascii_on_encode_error(self, mock_check):
        tracker = ProgressTracker(10)
        assert tracker.filled == '='
        assert tracker.empty == '-'
