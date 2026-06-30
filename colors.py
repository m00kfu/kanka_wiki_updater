"""
Small terminal-color helpers for the review CLI.

Uses colorama if it's installed -- it transparently makes ANSI colors work
in Windows terminals (PowerShell, cmd.exe) as well as Linux/Mac, which raw
ANSI codes don't always do on their own. Falls back to plain, uncolored
text if colorama isn't installed, so a missing optional dependency never
breaks the script -- review just looks plainer.
"""

try:
    from colorama import Fore, Style
    from colorama import init as _colorama_init

    _colorama_init(autoreset=True)
    _ENABLED = True
except ImportError:
    _ENABLED = False


def _identity(text):
    return text


def _wrap(code):
    def wrapper(text):
        return f'{code}{text}{Style.RESET_ALL}'

    return wrapper


if _ENABLED:
    red = _wrap(Fore.RED)
    green = _wrap(Fore.GREEN)
    yellow = _wrap(Fore.YELLOW)
    cyan = _wrap(Fore.CYAN)
    magenta = _wrap(Fore.MAGENTA)
    bold = _wrap(Style.BRIGHT)
    dim = _wrap(Style.DIM)
else:
    red = green = yellow = cyan = magenta = bold = dim = _identity
