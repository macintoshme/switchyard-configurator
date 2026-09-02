"""Common validation error class used across the Switchyard configurator.

All validation helpers raise :class:`ValidationError` instead of returning a
string.  The FastAPI layer catches the exception and translates it into a
structured JSON response (``{"ok": false, "field": ..., "error": ...}``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ValidationError(RuntimeError):
    """Raised when a validation rule fails.

    * ``field`` – a short identifier (usually the form label) that the UI can
      display next to the offending input.
    * ``message`` – the human‑readable error description.
    """

    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"
