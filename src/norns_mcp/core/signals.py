"""Turn SIGTERM / SIGHUP into ``SystemExit`` so ``finally: control.close()`` runs.

Without this a ``kill`` or a closed terminal ends the process with held notes still
down on the M8 and the clock lock left behind.
"""

from __future__ import annotations

import contextlib
import signal
from collections.abc import Callable, Iterator
from typing import Any

EXIT_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


@contextlib.contextmanager
def exit_on_signals(code: Callable[[int], int]) -> Iterator[None]:
    """Inside the block, each of ``EXIT_SIGNALS`` raises ``SystemExit(code(signum))``.

    Previous handlers are restored on the way out. Off the main thread (where Python
    cannot install handlers) this does nothing.
    """

    def handler(signum: int, frame: Any) -> None:
        raise SystemExit(code(signum))

    previous: dict[int, Any] = {}
    try:
        for sig in EXIT_SIGNALS:
            previous[sig] = signal.signal(sig, handler)
    except ValueError:  # not in the main thread
        pass
    try:
        yield
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old if old is not None else signal.SIG_DFL)


def shield_signals() -> None:
    """Ignore ``EXIT_SIGNALS`` from here on: call it just before ``close()`` so a second
    signal cannot abort the note-offs half way. ``exit_on_signals`` restores on exit."""
    with contextlib.suppress(ValueError):  # not in the main thread
        for sig in EXIT_SIGNALS:
            signal.signal(sig, signal.SIG_IGN)
