"""Demo: Terminal TUI review interface for Kanka Wiki Updater (Textual).

Run from the project root:
    python kanka_wiki_updater/demo_tui.py

Uses mock data — no Kanka API calls. Press q or Esc to quit.
"""

from textual.app import App
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.widgets import (
    Static,
    RichLog,
    Button,
    Footer,
    Header,
    TextArea,
)
from textual.binding import Binding
from typing import Iterable


# ── Mock data mimicking real sync_pipeline output ───────────────────────────

MOCK_NEW_ENTITIES = [
    {
        "proposal_type": "new_entity",
        "entity_name": "Vexara the Veiled",
        "suggested_type": "character",
        "draft_entry": "A mysterious sorceress who operates out of the Shadowmere district. She trades in forbidden knowledge and has been seen conversing with members of the Crimson Circle.",
        "source_journal": "Session 12 - The Gathering Storm",
    },
    {
        "proposal_type": "new_entity",
        "entity_name": "Ironhold Keep",
        "suggested_type": "location",
        "draft_entry": "An ancient fortress on the northern border, once held by the Frostborn dynasty. Now abandoned after the Great Schism.",
        "source_journal": "Session 12 - The Gathering Storm",
    },
]

MOCK_UPDATES = [
    {
        "proposal_type": "update",
        "entity_name": "Kael Ironfist",
        "entity_kind": "character",
        "entity_id": "42",
        "entity_local_id": 101,
        "source_journal": "Session 13 - Ashes of the North",
        "change_summary": "Updated synopsis to reflect his journey through Ironhold Keep and alliance with Vexara.",
        "previous_entry": "<p>Kael is a dwarf warrior from the Mountain Clan. He wields a massive warhammer called Stonebreaker. Known for his stubbornness and loyalty, he has been allies with Thorin for years.</p>",
        "proposed_entry": "<p>Kael is a dwarf warrior from the Mountain Clan who now serves as a scout for the Northern Alliance. He wields a massive warhammer called Stonebreaker and has recently formed an uneasy alliance with the sorceress Vexara after discovering ancient dwarven ruins at Ironhold Keep.</p>\n\n<p>His journey through the northern wastes has hardened him, but he remains loyal to his companions. The encounter at Ironhold revealed fragments of a prophecy that may explain the Great Schism.</p>",
        "relation_changes": [
            {
                "action": "create",
                "relation": "ally",
                "target_name": "Vexara the Veiled",
                "attitude": "cautious trust",
                "reason": "They formed an alliance after discovering Ironhold together.",
            },
        ],
    },
    {
        "proposal_type": "update",
        "entity_name": "Thorin Stormborn",
        "entity_kind": "character",
        "entity_id": "17",
        "entity_local_id": 88,
        "source_journal": "Session 13 - Ashes of the North",
        "change_summary": "Added new enemy relation and updated synopsis with his confrontation at the Frostpeak Pass.",
        "previous_entry": "<p>Thorin is an elven ranger who serves as Kael's closest friend. He has exceptional archery skills and knowledge of ancient lore. His family was destroyed during the Dragon Wars.</p>",
        "proposed_entry": "<p>Thorin is an elven ranger who serves as Kael's closest friend and tactical advisor. He has exceptional archery skills and deep knowledge of ancient dwarven architecture — which proved invaluable at Ironhold Keep.</p>\n\n<p>After the Frostpeak incident, Thorin has sworn vengeance against the Shadowmere cultists. His calm demeanor is beginning to crack under the weight of recent losses.</p>",
        "relation_changes": [
            {
                "action": "create",
                "relation": "enemy",
                "target_name": "Shadowmere Cult",
                "attitude": "vengeful",
                "reason": "Cultists ambushed Thorin at Frostpeak Pass, wounding him severely.",
            },
        ],
    },
]


# ── Color helpers (matching project's colors.py) ─────────────────────────────

GREEN = "#00ff00"
RED = "#ff4444"
CYAN = "#00ffff"
MAGENTA = "#ff00ff"
DIM = "#888888"
YELLOW = "#ffff00"


def green(t): return f"[{GREEN}]{t}[/{GREEN}]"
def red(t): return f"[{RED}]{t}[/{RED}]"
def cyan(t): return f"[{CYAN}]{t}[/{CYAN}]"
def magenta(t): return f"[{MAGENTA}]{t}[/{MAGENTA}]"
def dim(t): return f"[{DIM}]{t}[/{DIM}]"
def yellow(t): return f"[{YELLOW}]{t}[/{YELLOW}]"


# ── The TUI App ─────────────────────────────────────────────────────────────

class ReviewTUI(App):
    """A terminal-based review interface for Kanka wiki proposals."""

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("n", "next", "Next proposal"),
        Binding("p", "prev", "Previous"),
        Binding("a", "approve_all", "Approve all (synopsis + relations)"),
        Binding("s", "approve_synopsis", "Approve synopsis only, skip relations"),
        Binding("r", "reject", "Reject"),
        Binding("e", "toggle_edit", "Edit synopsis text"),
    ]

    CSS = """
    Screen {
        layout: grid;
        grid-size: 3;
        grid-gutter: 1 2;
    }

    #left-panel {
        width: 30%;
        height: 100%;
        border: bold $primary;
        title-align: center;
        title-background: black;
        title-color: $primary-lighten-1;
    }

    #right-panel {
        width: 70%;
        height: 100%;
        layout: vertical;
    }

    #proposal-list {
        height: 10fr;
        border: none;
    }

    .proposal-item {
        padding: 1 2;
        margin: 1;
        width: 100%;
        min-height: 3fr;
        content-align: left middle;
    }

    .proposal-item.selected {
        background: $primary-darken-1;
        text-style: bold;
    }

    #diff-view {
        height: 45%;
        border: none;
    }

    #edit-area-container {
        height: 30%;
        border: solid $secondary;
        display: none;
    }

    #edit-area-container.visible {
        display: block;
    }

    #edit-label {
        text-align: center;
        content-align: center middle;
        width: 100%;
        background: $secondary;
        color: white;
    }

    #actions-bar {
        height: 3fr;
        dock: bottom;
        border: none;
    }

    #actions-bar Button {
        min-width: 20;
        margin: 1;
    }

    #status-bar {
        height: 4fr;
        border: solid $warning;
        padding: 1 2;
    }

    #header-banner {
        width: 100%;
        content-align: center middle;
        background: $primary-darken-2;
        color: white;
        text-align: center;
        height: auto;
        padding: 1 2;
    }

    #info-bar {
        width: 100%;
        height: auto;
        padding: 1 2;
    }

    TextArea {
        background: $surface-darken-1;
        color: white;
    }
    """

    def __init__(self):
        super().__init__()
        self.all_proposals = []
        for p in MOCK_NEW_ENTITIES:
            p["status"] = "pending"
            self.all_proposals.append(p)
        for p in MOCK_UPDATES:
            p["status"] = "pending"
            self.all_proposals.append(p)
        self.selected_index = 0
        self.editing = False

    def compose(self) -> Iterable:
        yield Header()

        # Left panel — proposal list
        with Container(id="left-panel"):
            yield Static("[ ] PROPOSALS", id="list-header")
            for i, p in enumerate(self.all_proposals):
                kind = "NEW" if p["proposal_type"] == "new_entity" else "UPD"
                label = f"{kind} {p['entity_name']}"
                yield Static(
                    label,
                    classes="proposal-item",
                    id=f"prop-{i}",
                )

        # Right panel — details
        with ScrollableContainer(id="right-panel"):
            yield Static("─── PROPOSAL DETAILS ───", id="header-banner")
            yield Static("", id="info-bar")
            yield RichLog(id="diff-view", wrap=True, highlight=True)
            
            # Edit area (hidden by default)
            with Container(id="edit-area-container"):
                yield Static("  EDIT SYNOPSIS [e to finish editing]", id="edit-label")
                from textual.widgets import TextArea
                self._text_area = TextArea(
                    language="html",
                    spell_check=False,
                    tab_behavior="tab",
                    wrap=True,
                )
                yield self._text_area

            with Horizontal(id="actions-bar"):
                yield Button("✓ Approve All (a)", id="btn-approve-all")
                yield Button("📝 Synopsis Only (s)", id="btn-approve-synopsis")
                yield Button("✗ Reject (r)", id="btn-reject")
                yield Static("", id="spacer")
                yield Static(dim("[n]ext [p]rev [e]dit [q]uit"), id="help-text")

        yield Footer()

    def on_mount(self) -> None:
        self._render_selected()

    def _get_proposal_text(self, p):
        if p["proposal_type"] == "new_entity":
            return f"NEW {p['suggested_type'].upper()}: {p['entity_name']}"
        return f"{p['entity_kind'].capitalize()}: {p['entity_name']}"

    def _render_selected(self):
        """Render the currently selected proposal into the right panel."""
        idx = self.selected_index
        p = self.all_proposals[idx]
        
        # Update list selection styling
        for i in range(len(self.all_proposals)):
            widget = self.query_one(f"#prop-{i}", Static)
            if i == idx:
                widget.classes = "proposal-item selected"
            else:
                widget.classes = "proposal-item"

        # Header banner
        header = self.query_one("#header-banner", Static)
        kind_label = p.get("suggested_type", "").upper() or ""
        if p["proposal_type"] == "new_entity":
            header.update(
                f'  ═══ NEW {kind_label}: {p["entity_name"]} ═══'
            )
        else:
            status_icon = {"pending": "○", "applied": green("✓"), "rejected": red("✗")}.get(p.get("status", ""), "○")
            journal_ref = p.get("source_journal", "")
            header.update(
                f'  {status_icon} {p["entity_name"]} ({p["entity_kind"]}) '
                f'{dim(f"\u2190 {journal_ref}")}'
            )

        # Info bar — summary + warnings
        info = self.query_one("#info-bar", Static)
        lines = []
        if p.get("change_summary"):
            lines.append(dim("Summary: ") + p["change_summary"])
        
        # Check for dropped mentions (simplified)
        old_ids = set()
        new_ids = set()
        for tok in p.get("previous_entry", "").split("[entity:"):
            if "]" in tok:
                old_ids.add(tok.split("]")[0])
        for tok in p.get("proposed_entry", "").split("[entity:"):
            if "]" in tok:
                new_ids.add(tok.split("]")[0])
        dropped = old_ids - new_ids
        if dropped:
            lines.append(red(f"  !! {len(dropped)} mention link(s) missing from new version!"))

        # Unlinked mentions warning (simplified)
        unlinked_names = ["Vexara", "Ironhold"]
        for name in unlinked_names:
            if name not in p.get("proposed_entry", "") and name not in p.get("previous_entry", ""):
                pass
        
        info.update("\n".join(lines) if lines else dim("(no additional notes)"))

        # Diff view
        diff_log = self.query_one("#diff-view", RichLog)
        diff_log.clear()
        
        prev = p.get("previous_entry", "(none)")
        proposed = p.get("proposed_entry", "")
        
        if p["proposal_type"] == "new_entity":
            diff_log.write(dim("[ Draft Synopsis ]"))
            diff_log.write(proposed)
        else:
            # Show a simplified unified diff
            from difflib import unified_diff
            prev_lines = prev.splitlines() or ["(empty)"]
            new_lines = proposed.splitlines() or ["(none)"]
            
            diff_log.write(dim("── Synopsis Diff ──"))
            for line in unified_diff(prev_lines, new_lines, fromfile="old", tofile="new", lineterm=""):
                if line.startswith("+") and not line.startswith("+++"):
                    diff_log.write(f"[green]{line}[/green]")
                elif line.startswith("-") and not line.startswith("---"):
                    diff_log.write(f"[red]{line}[/red]")
                elif line.startswith("@@"):
                    diff_log.write(f"[dim]{line}[/dim]")
                else:
                    diff_log.write(line)

        # Relation changes
        if p.get("relation_changes"):
            diff_log.write("\n[dim]── Relationship Changes ──[/dim]")
            for rc in p["relation_changes"]:
                action_icon = {"create": green("+"), "update": yellow("~"), "delete": red("-")}.get(
                    rc.get("action", ""), "?"
                )
                diff_log.write(
                    f"  {action_icon} [{rc['action']}] {p['entity_name']} --{rc['relation']}--> "
                    f"{rc['target_name']} ({rc.get('attitude', 'N/A')})\n[dim]     Reason: {rc.get('reason', '')}[/dim]"
                )

        # Status indicator
        status = p.get("status", "pending")
        if status != "pending":
            diff_log.write(f"\n{'='*50}\n[dim]Status: {p['status'].upper()}[/dim]")

        # Hide edit area when switching proposals
        self._hide_edit_area()

    def _show_edit_area(self):
        """Show the inline editor for the synopsis."""
        p = self.all_proposals[self.selected_index]
        if p["proposal_type"] != "update":
            return
        
        self.editing = True
        container = self.query_one("#edit-area-container", Container)
        container.add_class("visible")
        
        # Strip HTML for editing (simplified — just show raw text)
        import re
        raw_text = re.sub(r'<[^>]+>', '', p["proposed_entry"])
        self._text_area.value = raw_text

    def _hide_edit_area(self):
        """Hide the inline editor."""
        self.editing = False
        container = self.query_one("#edit-area-container", Container)
        container.remove_class("visible")

    # ── Actions ───────────────────────────────────────────────────────────

    def action_next(self):
        if self.selected_index < len(self.all_proposals) - 1:
            self.selected_index += 1
            self._render_selected()

    def action_prev(self):
        if self.selected_index > 0:
            self.selected_index -= 1
            self._render_selected()

    def action_approve_all(self):
        p = self.all_proposals[self.selected_index]
        p["status"] = "applied"
        self._flash(green(f" ✓ Approved all: {p['entity_name']}"))
        self._render_selected()

    def action_approve_synopsis(self):
        p = self.all_proposals[self.selected_index]
        p["status"] = "applied"
        self._flash(yellow(f" 📝 Synopsis approved, relations skipped: {p['entity_name']}"))
        self._render_selected()

    def action_reject(self):
        p = self.all_proposals[self.selected_index]
        p["status"] = "rejected"
        self._flash(red(f" ✗ Rejected: {p['entity_name']}"))
        self._render_selected()

    def action_toggle_edit(self):
        if self.editing:
            # Save edits back to proposal
            import re
            raw = self._text_area.value
            html_text = f"<p>{raw.strip()}</p>"
            p = self.all_proposals[self.selected_index]
            p["proposed_entry"] = html_text
            self._flash(green("  Changes saved"))
            self._hide_edit_area()
        else:
            self._show_edit_area()

    def _flash(self, message):
        """Show a brief status flash at the bottom."""
        banner = self.query_one("#header-banner", Static)
        old_text = banner.renderable
        banner.update(message)
        self.call_later(lambda: banner.update(old_text))


if __name__ == "__main__":
    app = ReviewTUI()
    app.run()
