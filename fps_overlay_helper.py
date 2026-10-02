#!/usr/bin/env python3
"""
Proceso auxiliar standalone: el overlay de FPS estilo BlueStacks/emulador.

Por que un proceso aparte en vez de una ventana mas de la GUI principal:
un QWidget de Qt con "quedate siempre arriba" (lo que se probo primero)
no puede garantizar nada de eso en Wayland/Hyprland -- ahi el cliente no
tiene permiso para posicionarse a si mismo en la pantalla ni para forzar
estar por encima de otras ventanas (ni hablar de una en pantalla
completa como scrcpy). Eso lo decide el compositor.

La forma correcta en Wayland es el protocolo wlr-layer-shell: la misma
capa que usan cosas como waybar o las notificaciones, que SI estan
garantizadas por encima de las ventanas normales. GtkLayerShell es la
libreria que expone ese protocolo de forma simple, pero solo tiene
bindings para GTK -- no para Qt -- y mezclar dos toolkits graficos (Qt
+ GTK) con dos bucles de eventos en el mismo proceso es fragil. Por eso
este overlay corre como su propio proceso GTK3, y se comunica con
NexoHub (que sigue siendo 100% Qt) por su entrada estandar: cada linea
que llega es un numero de FPS, o "SHOW"/"HIDE". Se cierra solo cuando
NexoHub cierra ese pipe (stdin llega a EOF).

Requiere: python3-gi + GTK3 + gtk-layer-shell (paquete del sistema,
NO de pip -- ver detectors/dependencies.py). Si algo de esto falta,
NexoHub se da cuenta antes de intentar arrancar este script y usa un
overlay de respaldo mas simple.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, GtkLayerShell, GLib  # noqa: E402

# Mismos colores/umbrales que el resto de NexoHub (ver COLORS en gui.py).
GREEN = "#34d17f"
YELLOW = "#f2bd4e"
RED = "#ff5566"
SUBTEXT = "#9a9aa4"
ACCENT_BORDER = "#5c2129"

# El recuadro negro de fondo NO se pinta con esto -- ver el comentario en
# FpsBadge._on_draw sobre por que "app_paintable" obliga a dibujarlo a
# mano con Cairo. Esta CSS solo le da tipografia al numero.
CSS = """
#fpsLabel {
    font-family: sans-serif;
    font-weight: 800;
    font-size: 16px;
}
"""

BOX_RADIUS = 10
MARGIN = 28

# Mapea el nombre de posicion (el mismo que usa el combo de la GUI) al
# par de bordes de wlr-layer-shell a los que hay que anclar la ventana.
_POSITIONS = {
    "top-left": (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.LEFT),
    "top-right": (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.RIGHT),
    "bottom-left": (GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT),
    "bottom-right": (GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.RIGHT),
}
_ALL_EDGES = (
    GtkLayerShell.Edge.TOP,
    GtkLayerShell.Edge.BOTTOM,
    GtkLayerShell.Edge.LEFT,
    GtkLayerShell.Edge.RIGHT,
)


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _rounded_rect_path(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    """Arma el contorno de un rectangulo con esquinas redondeadas en el
    contexto Cairo dado, sin dibujarlo todavia (eso lo decide quien
    llame: fill, stroke, o ambos)."""
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


_HYPRCTL = shutil.which("hyprctl")
_FOCUS_POLL_MS = 400


def _active_window_is_scrcpy() -> bool:
    """
    Sin hyprctl (compositor distinto de Hyprland) no hay forma generica
    de saber cual es la ventana activa, asi que se asume que si (se
    mantiene el comportamiento anterior: visible todo el tiempo mientras
    la transmision este activa). Con hyprctl, se consulta la ventana
    enfocada real y solo se considera "activa" si es la de scrcpy -- asi
    el overlay no queda flotando encima de otras ventanas (YouTube,
    musica, etc.) cuando el usuario cambia de ventana.
    """
    if _HYPRCTL is None:
        return True
    try:
        result = subprocess.run(
            [_HYPRCTL, "-j", "activewindow"],
            capture_output=True, text=True, timeout=1,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        # Falla puntual de hyprctl: mejor no ocultar de mas por un
        # error transitorio.
        return True
    class_name = str(data.get("class", "")).lower()
    initial_class = str(data.get("initialClass", "")).lower()
    return "scrcpy" in class_name or "scrcpy" in initial_class


def _color_for(value: float) -> str:
    if value <= 0:
        return SUBTEXT
    if value < 20:
        return RED
    if value < 45:
        return YELLOW
    return GREEN


class FpsBadge:
    def __init__(self, position: str = "bottom-left") -> None:
        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_name("fpsBadge")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_app_paintable(True)

        screen = self.window.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.window.set_visual(visual)

        # -- pedirle al compositor la capa "overlay" (por encima de todo,
        # incluidas ventanas en pantalla completa), sin reservar espacio
        # (exclusive_zone 0) y sin robarle el foco de teclado a nada. La
        # esquina exacta la decide set_position() (anchors + margins).
        GtkLayerShell.init_for_window(self.window)
        GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self.window, 0)
        self.set_position(position)

        self.label = Gtk.Label(label="FPS: --")
        self.label.set_name("fpsLabel")
        self.label.set_halign(Gtk.Align.CENTER)
        self.label.set_margin_top(8)
        self.label.set_margin_bottom(8)
        self.label.set_margin_start(16)
        self.label.set_margin_end(16)
        self.window.add(self.label)

        # El recuadro negro NO se puede dejar en manos del CSS: con
        # "app_paintable(True)" (necesario para la transparencia de la
        # ventana con wlr-layer-shell) GTK deja de pintar el
        # background-color/border-radius del tema/CSS del todo -- por
        # eso antes quedaba solo el numero del FPS flotando sin ningun
        # fondo detras, y se perdia contra colores parecidos del juego.
        # Se dibuja a mano con Cairo en vez de confiar en el estilo.
        self.window.connect("draw", self._on_draw)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # La visibilidad real combina dos cosas: si NexoHub pidio mostrar
        # la placa (hay una transmision corriendo) Y si la ventana
        # enfocada ahora mismo es la de scrcpy (ver _active_window_is_scrcpy).
        # Asi, aunque la transmision siga activa, la placa se esconde sola
        # en cuanto el usuario cambia a otra ventana (YouTube, musica,
        # etc.) y vuelve a aparecer al volver a scrcpy.
        self._stream_active = False
        self._scrcpy_focused = True
        GLib.timeout_add(_FOCUS_POLL_MS, self._poll_focus)

    def set_position(self, position: str) -> None:
        """Reancla la ventana a la esquina pedida. Se puede llamar en
        caliente (la placa ya visible salta a la nueva esquina al toque),
        no solo al crear la ventana."""
        v_edge, h_edge = _POSITIONS.get(position, _POSITIONS["bottom-left"])
        for edge in _ALL_EDGES:
            GtkLayerShell.set_anchor(self.window, edge, edge in (v_edge, h_edge))
            GtkLayerShell.set_margin(self.window, edge, MARGIN)

    def _on_draw(self, widget, cr) -> bool:
        """Pinta el recuadro negro casi solido detras del numero. Ver el
        comentario en __init__ sobre por que esto no se puede resolver
        solo con CSS. Devuelve False para que GTK siga dibujando el
        label encima con normalidad."""
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        _rounded_rect_path(cr, 1, 1, width - 2, height - 2, BOX_RADIUS)
        cr.set_source_rgba(0, 0, 0, 0.94)
        cr.fill_preserve()
        r, g, b = _hex_to_rgb(ACCENT_BORDER)
        cr.set_source_rgba(r, g, b, 1.0)
        cr.set_line_width(1.4)
        cr.stroke()
        return False

    def _make_click_through(self) -> None:
        """Que los clics le lleguen a lo que hay debajo (scrcpy), no a la
        placa de FPS. Si pycairo no esta disponible se lo salta sin
        romper nada -- el overlay se ve igual, solo que esa esquina
        puntual dejaria de ser 'transparente' a los clics."""
        try:
            import cairo
        except ImportError:
            return
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)

    def _refresh_visibility(self) -> None:
        if self._stream_active and self._scrcpy_focused:
            self.window.show_all()
            GLib.idle_add(self._make_click_through)
        else:
            self.window.hide()

    def _poll_focus(self) -> bool:
        focused = _active_window_is_scrcpy()
        if focused != self._scrcpy_focused:
            self._scrcpy_focused = focused
            self._refresh_visibility()
        return True  # seguir sondeando

    def show(self) -> None:
        self._stream_active = True
        self._refresh_visibility()

    def hide(self) -> None:
        self._stream_active = False
        self._refresh_visibility()

    def set_fps(self, value: float) -> None:
        color = _color_for(value)
        text = f"FPS: {value:.0f}" if value > 0 else "FPS: --"
        self.label.set_markup(f'<span foreground="{color}">{text}</span>')


def main() -> None:
    initial_position = sys.argv[1] if len(sys.argv) > 1 else "bottom-left"
    badge = FpsBadge(position=initial_position)
    badge.show()

    def on_stdin(channel, condition) -> bool:  # noqa: ARG001 (firma la exige GLib)
        if condition & GLib.IO_HUP:
            Gtk.main_quit()
            return False
        line = sys.stdin.readline()
        if not line:
            Gtk.main_quit()
            return False
        line = line.strip()
        if line == "HIDE":
            badge.hide()
        elif line == "SHOW":
            badge.show()
        elif line.startswith("POS:"):
            badge.set_position(line[4:].strip() or "bottom-left")
        elif line:
            try:
                badge.set_fps(float(line))
            except ValueError:
                pass
        return True

    channel = GLib.IOChannel.unix_new(sys.stdin.fileno())
    GLib.io_add_watch(channel, GLib.IO_IN | GLib.IO_HUP, on_stdin)
    Gtk.main()


if __name__ == "__main__":
    main()
