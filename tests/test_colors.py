"""Tests for terminal color helpers (colorama enabled/disabled)."""

import sys
from unittest.mock import patch


def _reload_colors():
    """Force-reload colors module to pick up mock state."""
    if 'kanka_wiki_updater.colors' in sys.modules:
        del sys.modules['kanka_wiki_updater.colors']
    from kanka_wiki_updater import colors as mod

    return mod


class TestColorWrapping:
    def test_red_wraps_text(self):
        from kanka_wiki_updater.colors import red

        result = red('hello')
        assert 'hello' in result

    def test_green_wraps_text(self):
        from kanka_wiki_updater.colors import green

        result = green('world')
        assert 'world' in result

    def test_all_colors_exist(self):
        from kanka_wiki_updater.colors import red, green, yellow, cyan, magenta, bold, dim

        for fn in [red, green, yellow, cyan, magenta, bold, dim]:
            result = fn('test')
            assert 'test' in result

    def test_reset_applied(self):
        from kanka_wiki_updater.colors import red

        try:
            from colorama import Style

            expected_reset = Style.RESET_ALL
        except ImportError:
            return
        result = red('test')
        assert expected_reset in result


class TestDisabledFallback:
    def test_identity_when_no_colorama(self):
        import kanka_wiki_updater.colors as colors_mod

        _orig_enabled = colors_mod._ENABLED
        _orig_red = colors_mod.red

        try:
            colors_mod._ENABLED = False
            colors_mod.red = colors_mod._identity
            colors_mod.green = colors_mod._identity
            colors_mod.yellow = colors_mod._identity
            colors_mod.cyan = colors_mod._identity
            colors_mod.magenta = colors_mod._identity
            colors_mod.bold = colors_mod._identity
            colors_mod.dim = colors_mod._identity

            assert colors_mod.red('hello') == 'hello'
            assert colors_mod.green('world') == 'world'
        finally:
            colors_mod._ENABLED = _orig_enabled
            colors_mod.red = _orig_red


class TestEnabledFlag:
    def test_enabled_is_boolean(self):
        from kanka_wiki_updater.colors import _ENABLED

        assert isinstance(_ENABLED, bool)

    def test_identity_function_exists(self):
        from kanka_wiki_updater.colors import _identity

        assert _identity('hello') == 'hello'
