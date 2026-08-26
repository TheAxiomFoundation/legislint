"""legislint: deterministic findings on congressional bill XML."""

from . import billxml, conforming, findings, textparse
from .findings import Finding, run_checks
from .textparse import lint_text, parse_text

__version__ = "0.1.0"
__all__ = [
    "billxml",
    "conforming",
    "findings",
    "textparse",
    "Finding",
    "run_checks",
    "parse_text",
    "lint_text",
]
