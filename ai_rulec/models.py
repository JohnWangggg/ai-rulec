from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Rule:
    source: str
    path: str
    lineno: int
    kind: str
    value: str
    service_hint: str | None = None
    set_name: str | None = None
    requires_mitm: bool = False
    raw: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
