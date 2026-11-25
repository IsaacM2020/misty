"""Typed moves (step D). Importing this package registers the built-in moves."""
from . import basic, calendar, chatlink, classmoves, messages, work  # noqa: F401
from .registry import MOVES, catalog, pick_lines, recent, run, undo  # noqa: F401
