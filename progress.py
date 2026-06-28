"""In-memory progress tracker for sync_pipeline journal processing.

Uses \\r carriage return to overwrite the same terminal line so the user
always sees current progress without spammy output.
"""
import sys


class ProgressTracker:
    """Track per-journal work units with an in-place Unicode progress bar."""

    def __init__(self, total: int) -> None:
        self.total = max(total, 1)  # avoid division by zero if total is 0
        self.done = 0
        self._unicode = self._check_unicode()

    @classmethod
    def _check_unicode(cls):
        try:
            "█".encode(sys.stdout.encoding or "utf-8")
            return True
        except (UnicodeEncodeError, LookupError):
            return False

    @property
    def filled(self):
        return "█" if self._unicode else "="

    @property
    def empty(self):
        return "░" if self._unicode else "-"

    def _render(self, label: str = "") -> str:
        """Return a progress bar string like '[████░░ 60%] [LLM for Alice...]'.

        Uses full block chars (█) for done portion and empty blocks (░) for remaining.
        Falls back to ASCII ('=' / '-') on terminals that don't support Unicode.
        Percentage rounds up to avoid showing 100% until the final mark_done().
        """
        pct = min(int((self.done / self.total) * 100), 100) if self.total else 0
        filled_count = min(int(self.done / self.total * 20), 20)
        empty_count = 20 - filled_count

        bar = self.filled * filled_count + self.empty * empty_count
        header = f"[{bar} {pct}%]"
        if label:
            header += f" {label}"
        return header

    def mark_done(self, label: str = "") -> None:
        """Increment done count and render the bar in-place."""
        self.done += 1
        sys.stdout.write(f"\r{self._render(label)}")
        sys.stdout.flush()

    def finish(self) -> None:
        """Final render with newline so cursor is clean for next journal or prompt."""
        if self.total == 0:
            return  # nothing to show
        sys.stdout.write(f"\r{self._render('')}\n")
        sys.stdout.flush()
