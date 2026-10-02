"""Detecta resolucion y refresh rate de los monitores conectados."""
from __future__ import annotations

from dataclasses import dataclass

try:
    from screeninfo import get_monitors
except ImportError:  # pragma: no cover
    get_monitors = None


@dataclass
class Monitor:
    name: str
    width: int
    height: int
    is_primary: bool
    x: int = 0
    y: int = 0

    @property
    def long_side(self) -> int:
        return max(self.width, self.height)


class MonitorError(RuntimeError):
    pass


def detect_all() -> list[Monitor]:
    if get_monitors is None:
        raise MonitorError(
            "Falta la libreria screeninfo. Instalala con: pip install screeninfo"
        )
    monitors = []
    for m in get_monitors():
        monitors.append(
            Monitor(
                name=getattr(m, "name", "unknown") or "unknown",
                width=m.width,
                height=m.height,
                is_primary=bool(getattr(m, "is_primary", False)),
                x=getattr(m, "x", 0) or 0,
                y=getattr(m, "y", 0) or 0,
            )
        )
    if not monitors:
        raise MonitorError("No se detecto ningun monitor conectado.")
    return monitors


def pick_target(monitors: list[Monitor], preferred_name: str | None = None) -> Monitor:
    """Elige el monitor a usar: por nombre, primario, o el primero de la lista."""
    if preferred_name:
        for m in monitors:
            if m.name == preferred_name:
                return m
    for m in monitors:
        if m.is_primary:
            return m
    return monitors[0]
