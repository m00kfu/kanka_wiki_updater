#!/usr/bin/env python3
"""Entry point for running the review web UI.

Usage:
    python -m kanka_wiki_updater.review.web
"""

from kanka_wiki_updater.review.web import create_app

app = create_app()
app.run(host='127.0.0.1', port=5555)
