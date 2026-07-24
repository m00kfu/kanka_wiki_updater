"""Queue I/O and in-memory manipulation for pending changes.

This module owns all read/write operations on ``pending_changes.json`` as well
as in-place data manipulation (editing proposal text, updating status, managing
relation changes).  It is importable standalone — no Flask or CLI dependencies.

Usage
-----
    from kanka_wiki_updater.review.queue_manager import load_queue, save_queue, edit_proposal_text, ...

All functions are pure (load/save excepted) and operate on a queue list
returned by :func:`load_queue`.
"""

import os as _os
from pathlib import Path

# ---------------------------------------------------------------------------
# Dynamic imports for package + direct-execution compatibility
# ---------------------------------------------------------------------------

if __name__ == '__main__' and __package__ is None:
    _sys_path_0 = str(Path(__file__).resolve().parent.parent)
    if _sys_path_0 not in __import__('sys').path:
        __import__('sys').path.insert(0, _sys_path_0)

try:
    from ..core import config as pkg_config
except ImportError:
    from kanka_wiki_updater.core import config as pkg_config

try:
    from ..core import state
except ImportError:
    from kanka_wiki_updater.core import state


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def _queue_path():
    """Compute the path to pending_changes.json at call time.

    This allows tests to override ``config.DATA_DIR`` after module import
    without needing to reload queue_manager.
    """
    return _os.path.join(pkg_config.DATA_DIR, 'pending_changes.json')


def load_queue():
    """Load *pending_changes.json* and return the queue list.

    Returns an empty list when the file does not exist (same as
    :func:`state._load` default behaviour).
    """
    return state._load(_queue_path(), [])


def save_queue(queue):
    """Persist *queue* to *pending_changes.json*.

    The queue is serialised as pretty-printed JSON.  Any previous content is
    overwritten atomically (single write).
    """
    state._save(_queue_path(), queue)


# ---------------------------------------------------------------------------
# In-memory manipulation helpers
# ---------------------------------------------------------------------------


def edit_proposal_text(queue, index, text, proposal_type):
    """Update the draft or proposed entry text for a queue entry.

    Parameters
    ----------
    queue : list[dict]
        The current in-memory queue (mutated in-place).
    index : int
        Position of the target proposal.
    text : str
        The new text to store.
    proposal_type : str
        ``'new_entity'`` → sets ``draft_entry``; anything else →
        sets ``proposed_entry``.

    Raises
    ------
    IndexError
        If *index* is out of range (same as bare dict access).
    """
    if proposal_type == 'new_entity':
        queue[index]['draft_entry'] = text
    else:
        queue[index]['proposed_entry'] = text


def update_status(queue, index, status_value):
    """Update the status field of a queue entry.

    Parameters
    ----------
    queue : list[dict]
    index : int
    status_value : str
        One of ``'approved_all'``, ``'approved_synopsis_only'``, or
        ``'rejected'``.  The first two map to the stored value ``'applied'``;
        ``'rejected'`` maps to ``'rejected'``.

    Returns
    -------
    str | None
        The *new* status string (e.g. ``'applied'``), or ``None`` if the
        input was invalid.
    """
    valid = {'approved_all', 'approved_synopsis_only', 'rejected'}
    if status_value not in valid:
        return None

    queue[index]['status'] = 'applied' if status_value != 'rejected' else 'rejected'
    return queue[index]['status']


# ---------------------------------------------------------------------------
# Relation CRUD helpers
# ---------------------------------------------------------------------------


def add_relation_change(queue, index, action, target_name, relation='', attitude='', reason=''):
    """Add a new relation change to the proposal's ``relation_changes`` list.

    Parameters
    ----------
    queue : list[dict]
    index : int
    action : str
        One of ``'create'``, ``'update'``, or ``'delete'``.
    target_name : str
        The name of the entity this relation targets.
    relation : str
        The Kanka relation type (e.g. ``'ally'``, ``'enemy'``).
    attitude : str
        Optional attitude descriptor.
    reason : str
        Optional reason for the change.

    Returns
    -------
    dict
        The newly-created relation-change dict (for inspection by callers).
    """
    if 'relation_changes' not in queue[index]:
        queue[index]['relation_changes'] = []
    entry = {
        'action': action,
        'target_name': target_name,
        'relation': relation,
        'attitude': attitude,
        'reason': reason,
    }
    queue[index]['relation_changes'].append(entry)
    return entry


def delete_relation_change(queue, index, target_name):
    """Remove a relation change by *target_name* from the proposal.

    Returns
    -------
    bool
        ``True`` if a matching entry was found and removed; ``False`` otherwise.
    """
    rels = queue[index].get('relation_changes', [])
    for i, rc in enumerate(rels):
        if rc.get('target_name') == target_name:
            rels.pop(i)
            return True
    return False


def update_relation_change(queue, index, target_name, **fields):
    """Update fields on an existing relation change identified by *target_name*.

    Parameters
    ----------
    queue : list[dict]
    index : int
    target_name : str
        The name of the entity whose relation entry to update.
    **fields
        Key-value pairs to set (e.g. ``relation='rival'``, ``attitude='distrust'``).

    Returns
    -------
    dict | None
        The updated relation-change dict, or ``None`` if no matching entry was
        found.
    """
    rels = queue[index].get('relation_changes', [])
    for rc in rels:
        if rc.get('target_name') == target_name:
            rc.update(fields)
            return rc
    return None
