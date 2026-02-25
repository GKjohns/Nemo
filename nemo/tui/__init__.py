"""Nemo interactive TUI entrypoint."""


def launch_tui() -> None:
    """Lazy import and run the Rich-based interactive REPL."""
    from .app import launch_tui as _launch

    _launch()


__all__ = ["launch_tui"]
