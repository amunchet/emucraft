"""Emucraft: fast G-code simulation, collision checking and feed optimization."""

__version__ = "0.2.0"

__all__ = ["__version__", "check_file", "check_text", "parse_program"]


def __getattr__(name):  # lazy imports keep `import emucraft` cheap
    if name in ("check_file", "check_text", "parse_program"):
        from . import check

        return getattr(check, name)
    raise AttributeError(name)
