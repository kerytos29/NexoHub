#!/usr/bin/env python3
"""
Interfaz grafica de scrcpy-autoconfig / NexoHub (PyQt6).

Reusa el mismo stack que Ronin Hub para que, si mas adelante decides
fusionar esto como una pestana ahi, sea copiar los widgets tal cual.
Toda deteccion, instalacion y lanzamiento pesado corre en QThreads para
no congelar la ventana (mismo patron que se aplico en Ronin Hub).

La contrasena de root (para cpupower / power USB / instalar paquetes)
nunca se pide en la terminal: todo pasa por pkexec, que muestra el
dialogo grafico nativo de polkit.

Enfoque de esta version: dejar de sentirse "formulario" sin encerrar al
usuario en un asistente de pasos obligatorios. La pagina de
Configuracion es un panel unico: dispositivo, perfil de uso y ajustes
de transmision estan siempre a la vista y accesibles en cualquier
orden, con los perfiles como tarjetas grandes en vez de un combo. Todo
lo tecnico (codec/encoder/bitrate/flags, comando + registro, y el
diagnostico detallado del setup) vive en secciones colapsables aparte
("Opciones avanzadas", "Comando y registro", "Detalles tecnicos"),
cerradas por defecto, y solo se abren si el usuario las necesita o si
algo falla.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import command_builder
import main as core  # load_config, apply_profile, gather
from detectors import android_device
from detectors import bluetooth
from detectors import dependencies
from detectors import privileged
from detectors import resolution
from detectors import usb_topology
from detectors import wireless

CONFIG_PATH = Path(__file__).parent / "config.yaml"

APP_NAME = "NexoHub"
APP_ICON = "📱"
CUSTOM_PROFILE_KEY = "__custom__"

# --------------------------------------------------------------------------
# Tema visual: negro + rojo
# --------------------------------------------------------------------------
COLORS = {
    "base": "#0b0d12",
    "mantle": "#12151c",
    "crust": "#080a0e",
    "surface0": "#1a1e27",
    "surface1": "#242935",
    "surface2": "#343b4a",
    "surface3": "#4a5162",
    "field": "#11141b",
    "text": "#eef0f4",
    "subtext": "#8b93a3",
    # Rojo de marca (el mismo que ya usaban el icono del instalador y el
    # borde del overlay de FPS) en vez del azul que se habia colado en
    # algun momento -- esto es lo que hace que la app se sienta "negro y
    # rojo" de punta a punta en vez de mezclar paletas sueltas.
    "accent": "#e0303f",
    "accent2": "#e0303f",
    "accent_hover": "#ff4d5e",
    "accent_active": "#b8232f",
    "accent_soft": "#2a1013",
    "accent_border": "#5c2129",
    "gold": "#e8b84b",
    "teal": "#3fc9c1",
    "green": "#3ddc97",
    "term_green": "#39ff6a",
    "yellow": "#f2bd4e",
    # Rojo de "peligro/error", deliberadamente mas rosado que el accent
    # de marca -- son dos rojos distintos a proposito (uno es identidad,
    # el otro es semantica de "algo salio mal"), para que no se confundan
    # un boton primario con un aviso de error.
    "red": "#ff5566",
}
# Boton primario en color solido: mas "panel de control profesional"
# que degradado -- el color hace un solo trabajo (llamar la atencion a
# LA accion principal), no decora.
GRADIENT_PRIMARY = COLORS["accent"]
GRADIENT_PRIMARY_HOVER = COLORS["accent_hover"]

STYLESHEET = f"""
QWidget {{
    background-color: {COLORS['base']};
    color: {COLORS['text']};
    font-size: 13px;
    font-family: "Inter", "Segoe UI", "Cantarell", "Ubuntu", sans-serif;
}}
QMainWindow {{
    background-color: {COLORS['crust']};
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QWidget#contentArea {{
    background-color: {COLORS['base']};
}}
QToolTip {{
    background-color: {COLORS['surface1']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['surface2']};
    border-radius: 8px;
    padding: 8px 11px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['surface2']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLORS['surface3']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QLabel#brand {{
    color: {COLORS['text']};
    font-size: 17px;
    font-weight: 800;
    letter-spacing: 0.3px;
}}
QLabel#brandSubtitle {{
    color: {COLORS['subtext']};
    font-size: 11px;
    letter-spacing: 0.2px;
}}
QPushButton#exitButton {{
    background-color: transparent;
    color: {COLORS['subtext']};
    border: none;
    border-radius: 10px;
    padding: 8px;
    font-size: 14px;
    font-weight: 600;
}}
QPushButton#exitButton:hover {{
    background-color: {COLORS['accent_soft']};
    color: {COLORS['red']};
}}

/* --- barra superior --- */
QWidget#topbar {{
    background-color: {COLORS['base']};
    border-bottom: 1px solid {COLORS['surface0']};
}}
QLabel[role="pill"] {{
    background-color: {COLORS['surface0']};
    color: {COLORS['subtext']};
    border: 1px solid {COLORS['surface1']};
    border-radius: 12px;
    padding: 4px 13px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#statusDot {{
    border-radius: 5px;
    min-width: 10px;
    max-width: 10px;
    min-height: 10px;
    max-height: 10px;
}}
QLabel#statusText {{
    color: {COLORS['text']};
    font-size: 12px;
    font-weight: 700;
}}
QFrame#statusChip {{
    background-color: {COLORS['surface0']};
    border: 1px solid {COLORS['surface1']};
    border-radius: 16px;
}}

/* --- contenedores de seccion (reemplazan al QGroupBox por defecto) --- */
QGroupBox {{
    background-color: {COLORS['mantle']};
    border: 1px solid {COLORS['surface0']};
    border-radius: 14px;
    margin-top: 20px;
    padding-top: 22px;
    padding-bottom: 4px;
    font-weight: 700;
    font-size: 13px;
    color: {COLORS['text']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 18px;
    top: 3px;
    padding: 0 6px;
    color: {COLORS['text']};
    letter-spacing: 0.2px;
}}
QPushButton {{
    background-color: {COLORS['surface0']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['surface1']};
    border-radius: 10px;
    padding: 9px 17px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {COLORS['surface1']};
    border: 1px solid {COLORS['surface2']};
}}
QPushButton:pressed {{
    background-color: {COLORS['surface0']};
}}
QPushButton:disabled {{
    color: {COLORS['subtext']};
    background-color: {COLORS['surface0']};
    border: 1px solid {COLORS['surface0']};
}}
QPushButton#primary {{
    background: {GRADIENT_PRIMARY};
    border: 1px solid {COLORS['accent']};
    color: #ffffff;
    font-weight: 700;
}}
QPushButton#primary:hover {{
    background: {GRADIENT_PRIMARY_HOVER};
    border: 1px solid {COLORS['accent_hover']};
}}
QPushButton#primary:pressed {{
    background-color: {COLORS['accent_active']};
    border: 1px solid {COLORS['accent_active']};
}}
QPushButton#primary:disabled {{
    background-color: {COLORS['surface0']};
    border: 1px solid {COLORS['surface0']};
    color: {COLORS['subtext']};
}}
QPushButton#bigAction {{
    padding: 13px 24px;
    font-size: 13px;
    border-radius: 11px;
}}
QFrame#actionBar {{
    background-color: {COLORS['field']};
    border: 1px solid {COLORS['surface0']};
    border-radius: 12px;
}}
QToolButton#helpButton {{
    background-color: {COLORS['surface0']};
    color: {COLORS['subtext']};
    border-radius: 10px;
    min-width: 20px;
    max-width: 20px;
    min-height: 20px;
    max-height: 20px;
    font-weight: 700;
}}
QToolButton#helpButton:hover {{
    background-color: {COLORS['accent']};
    color: #ffffff;
}}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    background-color: {COLORS['field']};
    border: 1px solid {COLORS['surface1']};
    border-radius: 10px;
    padding: 7px 11px;
    selection-background-color: {COLORS['accent']};
    selection-color: #ffffff;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{
    border: 1px solid {COLORS['surface2']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {COLORS['accent']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 16px;
    border: none;
    background: transparent;
}}
QLabel[role="hint"] {{
    color: {COLORS['subtext']};
    font-size: 12px;
    line-height: 140%;
}}
QLabel[role="field-hint"] {{
    color: {COLORS['subtext']};
    font-size: 11px;
}}
QCheckBox {{
    spacing: 9px;
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border-radius: 6px;
    border: 1px solid {COLORS['surface2']};
    background-color: {COLORS['field']};
}}
QCheckBox::indicator:hover {{
    border: 1px solid {COLORS['surface3']};
}}
QCheckBox::indicator:checked {{
    background: {GRADIENT_PRIMARY};
    border: 1px solid {COLORS['accent']};
}}

/* --- tarjetas de diagnostico / dependencias --- */
QFrame#diagCard, QFrame#depRow {{
    background-color: {COLORS['field']};
    border: 1px solid {COLORS['surface0']};
    border-left: 3px solid {COLORS['surface1']};
    border-radius: 12px;
}}
QFrame#diagCard:hover, QFrame#depRow:hover {{
    background-color: {COLORS['surface0']};
}}
QFrame#diagCard[empty="true"] {{
    border-left: 3px solid {COLORS['surface1']};
}}
QFrame#diagCard[empty="false"] {{
    border-left: 3px solid {COLORS['green']};
}}
QFrame#depRow[status="ok"] {{
    border-left: 3px solid {COLORS['green']};
}}
QFrame#depRow[status="missing"] {{
    border-left: 3px solid {COLORS['red']};
}}
QFrame#depRow[status="warn"] {{
    border-left: 3px solid {COLORS['yellow']};
}}
QLabel[role="card-label"] {{
    color: {COLORS['subtext']};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.2px;
}}
QLabel[role="card-value"] {{
    color: {COLORS['text']};
    font-size: 14px;
    font-weight: 600;
}}

/* --- tarjetas de perfil --- */
QFrame#profileCard {{
    background-color: {COLORS['field']};
    border: 1px solid {COLORS['surface0']};
    border-radius: 13px;
}}
QFrame#profileCard:hover {{
    background-color: {COLORS['surface0']};
    border: 1px solid {COLORS['surface2']};
}}
QFrame#profileCard[selected="true"] {{
    border: 2px solid {COLORS['accent']};
    background-color: {COLORS['accent_soft']};
}}
QLabel[role="profile-title"] {{
    color: {COLORS['text']};
    font-size: 14px;
    font-weight: 700;
}}
QLabel[role="profile-desc"] {{
    color: {COLORS['subtext']};
    font-size: 11px;
}}
QLabel#profileCheck {{
    color: {COLORS['accent']};
    font-size: 15px;
    font-weight: 800;
}}
QLabel#profileIcon {{
    border-radius: 9px;
    font-size: 15px;
}}

/* --- placeholder de "Mapeo de teclas" (todavia no implementado) --- */
QFrame#comingSoonBanner {{
    background-color: {COLORS['accent_soft']};
    border: 1px solid {COLORS['accent_border']};
    border-radius: 10px;
}}
QFrame#keymapMock {{
    background-color: {COLORS['field']};
    border: 1px dashed {COLORS['surface2']};
    border-radius: 10px;
}}

/* --- secciones colapsables --- */
QFrame#collapsible {{
    background-color: {COLORS['mantle']};
    border: 1px solid {COLORS['surface0']};
    border-radius: 12px;
}}
QToolButton#collapsibleHeader {{
    background-color: transparent;
    border: none;
    color: {COLORS['text']};
    font-weight: 700;
    font-size: 13px;
    padding: 14px 17px;
    text-align: left;
}}
QToolButton#collapsibleHeader:hover {{
    background-color: {COLORS['surface0']};
    border-radius: 14px;
}}

/* --- terminal (pagina Registro): monoespaciada y en verde, a proposito --
   es la unica pantalla de la app que se permite verse "tecnica" sin
   vueltas, porque es exactamente lo que es: el comando real y su salida
   cruda. */
QFrame#terminal {{
    background-color: #05070a;
    border: 1px solid #113322;
    border-radius: 12px;
}}
QPlainTextEdit#terminalView {{
    background-color: transparent;
    border: none;
    color: {COLORS['term_green']};
    font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", Consolas, monospace;
    font-size: 12.5px;
    selection-background-color: {COLORS['term_green']};
    selection-color: #05070a;
}}
"""



def _apply_shadow(widget: QWidget, blur: int = 28, alpha: int = 70) -> None:
    """Sombra sutil para dar sensacion de tarjeta elevada."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, 3)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


def _help_button(title: str, text: str, parent: QWidget | None = None) -> QToolButton:
    """Boton circular '?' que abre un dialogo con la explicacion completa
    de un campo. Complementa el tooltip para quien no pasa el mouse."""
    btn = QToolButton(parent)
    btn.setObjectName("helpButton")
    btn.setText("?")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.clicked.connect(lambda: QMessageBox.information(parent, title, text))
    return btn


INPUT_MODE_HELP = (
    "Como se manda tu teclado/mouse del PC al celular:\n\n"
    "• sdk (default de scrcpy): inyeccion estandar via ADB. La mas "
    "compatible, pero varios juegos competitivos la detectan como "
    "input sintetico y la bloquean.\n\n"
    "• uhid (recomendado): emula un teclado/mouse real a nivel de "
    "kernel de Android. La mayoria de los anti-cheat no lo distinguen "
    "de hardware real. Buen equilibrio compatibilidad/estabilidad.\n\n"
    "• aoa (Android Open Accessory): emula el protocolo USB real, aun "
    "mas dificil de detectar que uhid, pero mas exigente con el cable "
    "y puede ser menos estable en algunos setups.\n\n"
    "• disabled: solo transmite pantalla, sin mandar ningun input "
    "(agrega --no-control). Util para solo mirar o grabar."
)

ENCODER_HELP = (
    "El nombre exacto del encoder de video de hardware (ej. "
    "c2.mtk.avc.encoder, c2.qti.avc.encoder...) depende del chipset de "
    "CADA celular. Dejalo vacio para que siempre se detecte automatico "
    "el mejor disponible en el celular conectado -- forzarlo a mano solo "
    "tiene sentido si sabes el nombre exacto de tu chipset y quieres uno "
    "especifico."
)

# scrcpy con --print-fps imprime lineas tipo "INFO: 59 fps" cada segundo.
FPS_LINE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps", re.IGNORECASE)

# A partir de estos valores se avisa que la calidad va a mejorar pero el
# delay/fluidez pueden resentirse (USB 2.0, WiFi compartido, celulares
# de gama media, etc.).
FPS_WARN_THRESHOLD = 60
BITRATE_WARN_THRESHOLD_MBPS = 20.0


def _parse_bitrate_mbps(text: str) -> float | None:
    """Convierte '32M', '500K' o un numero puro en bps a Mbps. None si
    el texto esta vacio o no se puede interpretar."""
    text = text.strip().upper()
    if not text:
        return None
    try:
        if text.endswith("M"):
            return float(text[:-1])
        if text.endswith("K"):
            return float(text[:-1]) / 1000.0
        return float(text) / 1_000_000.0
    except ValueError:
        return None


PROFILE_DEFS = [
    # (key, icono, titulo, descripcion corta, tooltip largo, color de acento)
    (
        CUSTOM_PROFILE_KEY, "P", "Personalizado",
        "Ajustas todo a mano en Opciones avanzadas.",
        "No aplica ningun preset. Los valores son los que dejaste (o los "
        "que traiga config.yaml) en la seccion 'Opciones avanzadas'.",
        "surface2",
    ),
    (
        "competitive", "C", "Competitivo",
        "Minima latencia para juegos rapidos.",
        "Prioriza responsividad sobre calidad de imagen: bitrate y "
        "resolucion mas bajos, y fuerza frames clave frecuentes para "
        "reducir el 'ghosting' en movimientos rapidos. Pensado para "
        "juegos donde cada milisegundo de delay importa.",
        "accent",
    ),
    (
        "streaming", "S", "Directo / Streaming",
        "Buena calidad para el espectador, sin recalentar el celular.",
        "Pensado para transmitir en vivo: mejor calidad de imagen que "
        "'Competitivo', pero usando h264 (mas liviano para el encoder "
        "del celular que h265) y sin el flag agresivo de frames clave, "
        "para no exigir tanto al celular ni acortar su bateria durante "
        "sesiones largas.",
        "gold",
    ),
    (
        "quality", "Q", "Calidad",
        "Máxima nitidez, sin importar el bitrate.",
        "Prioriza la nitidez de imagen por sobre todo lo demas: usa "
        "h265 y el bitrate/resolucion mas altos. Ideal si vas a grabar "
        "o si tu conexion USB y celular aguantan sin problema.",
        "teal",
    ),
]


# --------------------------------------------------------------------------
# Workers (corren en QThread para no bloquear la UI)
# --------------------------------------------------------------------------
class DetectWorker(QThread):
    done = pyqtSignal(object)  # (device, usb, monitor, cpu_status)
    failed = pyqtSignal(str)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config

    def run(self) -> None:
        try:
            result = core.gather(self.config)
        except SystemExit:
            self.failed.emit(
                "No se pudo detectar el setup. Revisa que el celular este "
                "conectado y autorizado por ADB."
            )
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.done.emit(result)


class LaunchWorker(QThread):
    log = pyqtSignal(str)
    fps_update = pyqtSignal(float)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, command: list[str], usb, fix_usb: bool, fix_cpu: bool):
        super().__init__()
        self.command = command
        self.usb = usb
        self.fix_usb = fix_usb
        self.fix_cpu = fix_cpu
        self.process: subprocess.Popen | None = None
        self._stop_requested = False

    def run(self) -> None:
        if self.fix_usb or self.fix_cpu:
            try:
                touched = privileged.apply_privileged_fixes(self.usb, self.fix_usb, self.fix_cpu)
                if touched:
                    self.log.emit(f"USB power forzado a 'on' en: {touched}")
                if self.fix_cpu:
                    self.log.emit("CPU governor cambiado a 'performance'.")
            except privileged.PrivilegedActionError as exc:
                self.log.emit(f"No se pudieron aplicar los ajustes con privilegios: {exc}")

        self.log.emit("Comando: " + " ".join(self.command))
        try:
            # scrcpy, al no escribir a una terminal sino a un pipe, cambia
            # su salida de "por linea" a "por bloques" (buffering estandar
            # de libc) -- eso hacia que las lineas de --print-fps tardaran
            # demasiado en llegar (o nunca llegaran antes de detener
            # scrcpy). "stdbuf -oL -eL" fuerza salida por linea sin tocar
            # scrcpy en si. Si no esta disponible, se sigue sin el wrapper
            # (funciona igual, pero el overlay puede demorar en reaccionar).
            command = self.command
            stdbuf_path = shutil.which("stdbuf")
            if stdbuf_path:
                command = [stdbuf_path, "-oL", "-eL", *self.command]
            else:
                self.log.emit(
                    "Aviso: no se encontro 'stdbuf' (paquete coreutils). "
                    "El overlay de FPS puede tardar en actualizarse."
                )
            # stdout+stderr combinados y leidos linea a linea: es lo que
            # permite tanto loguear en vivo como parsear las lineas
            # "INFO: N fps" que imprime --print-fps para el overlay.
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return

        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.rstrip()
            if not line:
                continue
            match = FPS_LINE_RE.search(line)
            if match:
                try:
                    self.fps_update.emit(float(match.group(1)))
                except ValueError:
                    pass
            self.log.emit(line)

        returncode = self.process.wait()
        if self._stop_requested or returncode == 0:
            self.finished_ok.emit()
        else:
            self.failed.emit(f"scrcpy termino con codigo {returncode}.")

    def stop(self) -> None:
        """Pide que se detenga la transmision (boton 'Detener'). No
        bloquea: manda SIGTERM y deja que el bucle de lectura de stdout
        del hilo detecte la salida del proceso solo."""
        self._stop_requested = True
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:  # noqa: BLE001
                pass

    def kill(self) -> None:
        """Ultimo recurso si el proceso no respondio a stop() a tiempo."""
        if self.process and self.process.poll() is None:
            try:
                self.process.kill()
            except Exception:  # noqa: BLE001
                pass


class AdbRestartWorker(QThread):
    """Corre 'adb kill-server' + 'adb start-server' fuera del hilo de UI."""
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            message = android_device.restart_adb_server()
        except android_device.AndroidDeviceError as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(message)


class PhoneResolutionWorker(QThread):
    """Lee o cambia la resolucion de PANTALLA del celular via ADB ('adb
    shell wm size'), fuera del hilo de UI -- 'action' es 'get', 'set' o
    'reset'; 'width'/'height' solo aplican para 'set'."""
    done = pyqtSignal(object)  # detectors.resolution.ScreenSize
    failed = pyqtSignal(str)

    def __init__(self, serial: str, action: str, width: int = 0, height: int = 0):
        super().__init__()
        self.serial = serial
        self.action = action
        self.width = width
        self.height = height

    def run(self) -> None:
        try:
            if self.action == "set":
                resolution.set_size(self.serial, self.width, self.height)
            elif self.action == "reset":
                resolution.reset_size(self.serial)
            size = resolution.get_current(self.serial)
        except resolution.ResolutionError as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(size)


class WifiEnableWorker(QThread):
    """Cambia el celular de USB a WiFi/LAN (adb tcpip + adb connect)."""

    done = pyqtSignal(str)  # nueva direccion "ip:puerto"
    failed = pyqtSignal(str)

    def __init__(self, usb_serial: str, port: int):
        super().__init__()
        self.usb_serial = usb_serial
        self.port = port

    def run(self) -> None:
        try:
            address = wireless.enable_over_wifi(self.usb_serial, port=self.port)
        except wireless.WirelessError as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(address)


class WifiDisableWorker(QThread):
    """Cierra la conexion 'adb connect' inalambrica (adb disconnect)."""

    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, address: str):
        super().__init__()
        self.address = address

    def run(self) -> None:
        try:
            wireless.disconnect(self.address)
        except wireless.WirelessError as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit()


class BluetoothCheckWorker(QThread):
    """Chequea el estado de Bluetooth del PC (solo lectura, no empareja nada)."""

    done = pyqtSignal(object)  # BluetoothStatus
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            status = bluetooth.detect()
        except bluetooth.BluetoothError as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(status)


class DependencyCheckWorker(QThread):
    done = pyqtSignal(object)  # DependencyReport

    def run(self) -> None:
        self.done.emit(dependencies.check())


class DependencyInstallWorker(QThread):
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, report: dependencies.DependencyReport):
        super().__init__()
        self.report = report

    def run(self) -> None:
        try:
            if self.report.missing_binaries:
                self.log.emit(
                    "Pidiendo autenticacion grafica para instalar paquetes "
                    "del sistema..."
                )
                dependencies.install_missing_system_packages(self.report)
                self.log.emit("Paquetes del sistema instalados.")
            if self.report.missing_python:
                self.log.emit("Instalando paquetes de Python con pip...")
                dependencies.install_missing_python_packages(self.report)
                self.log.emit("Paquetes de Python instalados.")
        except dependencies.DependencyError as exc:
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit()


# --------------------------------------------------------------------------
# Tarjetas y widgets reutilizables
# --------------------------------------------------------------------------
class DiagCard(QFrame):
    """Tarjeta pequena icono+label+valor para el panel de diagnostico. El
    texto siempre se muestra completo (con wrap), nada queda cortado ni
    exige scroll para leerse."""

    def __init__(self, label: str, icon: str = ""):
        super().__init__()
        self.setObjectName("diagCard")
        self.setProperty("empty", "true")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(4)
        label_row = QHBoxLayout()
        label_row.setSpacing(6)
        label_row.setContentsMargins(0, 0, 0, 0)
        if icon:
            icon_label = QLabel(icon)
            icon_label.setFixedSize(20, 20)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_label.setStyleSheet(
                f"font-size: 12px; background-color: {COLORS['surface0']}; "
                f"border-radius: 5px;"
            )
            label_row.addWidget(icon_label)
        self._label = QLabel(label)
        self._label.setProperty("role", "card-label")
        label_row.addWidget(self._label)
        label_row.addStretch(1)
        self._value = QLabel("Sin detectar todavia")
        self._value.setProperty("role", "card-value")
        self._value.setWordWrap(True)
        self._value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addLayout(label_row)
        layout.addWidget(self._value)

    def set_value(self, text: str) -> None:
        self._value.setText(text)
        self.setProperty("empty", "false")
        self.style().unpolish(self)
        self.style().polish(self)


class DependencyRow(QFrame):
    """Fila-tarjeta para un requisito del sistema: icono de estado, nombre
    y detalle. Reemplaza la tabla cruda por algo mas facil de leer de
    un vistazo."""

    def __init__(self, name: str, detail: str):
        super().__init__()
        self.setObjectName("depRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        self._status_label = QLabel("…")
        self._status_label.setFixedWidth(90)
        layout.addWidget(self._status_label)

        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        name_label = QLabel(name)
        name_label.setProperty("role", "card-value")
        detail_label = QLabel(detail)
        detail_label.setProperty("role", "field-hint")
        text_box.addWidget(name_label)
        text_box.addWidget(detail_label)
        layout.addLayout(text_box, 1)

        self.set_status(ok=False, warn=False, unknown=True)

    def set_status(self, ok: bool, warn: bool = False, unknown: bool = False) -> None:
        if unknown:
            text, color, state = "…", COLORS["subtext"], "unknown"
        elif warn:
            text, color, state = "⚠ opcional", COLORS["yellow"], "warn"
        elif ok:
            text, color, state = "✓ instalado", COLORS["green"], "ok"
        else:
            text, color, state = "✗ falta", COLORS["red"], "missing"
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.setProperty("status", state)
        self.style().unpolish(self)
        self.style().polish(self)


class ProfileCard(QFrame):
    """Tarjeta grande y clickeable para elegir un perfil, en vez de un
    combo box escondido. El estado 'seleccionado' se ve en el borde."""

    clicked = pyqtSignal(str)

    def __init__(self, key: str, icon: str, title: str, description: str, tooltip: str, tint: str = "accent"):
        super().__init__()
        self.key = key
        self.setObjectName("profileCard")
        self.setProperty("selected", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self._tint = COLORS.get(tint, COLORS["accent"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(10)
        self._icon_label = QLabel(icon)
        self._icon_label.setObjectName("profileIcon")
        self._icon_label.setFixedSize(32, 32)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label = QLabel(title)
        title_label.setProperty("role", "profile-title")
        head.addWidget(self._icon_label)
        head.addWidget(title_label)
        head.addStretch(1)
        self._check_label = QLabel("✓")
        self._check_label.setObjectName("profileCheck")
        self._check_label.hide()
        head.addWidget(self._check_label)
        layout.addLayout(head)

        desc_label = QLabel(description)
        desc_label.setProperty("role", "profile-desc")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        self._apply_icon_style(selected=False)

    def _apply_icon_style(self, selected: bool) -> None:
        # En reposo, todos los iconos son neutros -- el color solo
        # aparece para SEÑALAR que esta tarjeta es la elegida, no como
        # decoracion distinta por tarjeta. Menos "arcoiris", mas claro.
        if selected:
            self._icon_label.setStyleSheet(
                f"background-color: {COLORS['accent_soft']}; border: 1px solid {self._tint};"
            )
            self._check_label.setStyleSheet(f"color: {self._tint}; font-size: 14px; font-weight: 800;")
        else:
            self._icon_label.setStyleSheet(
                f"background-color: {COLORS['surface0']}; border: 1px solid {COLORS['surface1']};"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        self.clicked.emit(self.key)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self._check_label.setVisible(selected)
        self._apply_icon_style(selected)
        self.style().unpolish(self)
        self.style().polish(self)


class CollapsibleSection(QFrame):
    """Seccion plegable: encabezado clickeable + contenido que se
    muestra/oculta. Sirve para esconder lo tecnico (codec, encoder,
    bitrate, comando generado...) sin borrarlo de la interfaz -- solo
    queda a un click de quien lo necesite."""

    def __init__(self, icon: str, title: str, expanded: bool = False):
        super().__init__()
        self.setObjectName("collapsible")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._icon = icon
        self._title = title
        self.toggle_btn = QToolButton()
        self.toggle_btn.setObjectName("collapsibleHeader")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(expanded)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._on_toggle)
        outer.addWidget(self.toggle_btn)

        self.content = QWidget()
        self.content.setVisible(expanded)
        outer.addWidget(self.content)
        self._update_header()

    def _update_header(self) -> None:
        arrow = "▾" if self.toggle_btn.isChecked() else "▸"
        if self._icon:
            self.toggle_btn.setText(f"{arrow}  {self._icon}  {self._title}")
        else:
            self.toggle_btn.setText(f"{arrow}  {self._title}")

    def _on_toggle(self) -> None:
        self.content.setVisible(self.toggle_btn.isChecked())
        self._update_header()

    def set_expanded(self, expanded: bool) -> None:
        if self.toggle_btn.isChecked() == expanded:
            return
        self.toggle_btn.setChecked(expanded)
        self._on_toggle()


class _LegacyFpsOverlay(QWidget):
    """Overlay de respaldo: ventana Qt comun, siempre encima, con el FPS
    en vivo. Se usa solo si gtk-layer-shell no esta disponible (ver
    FpsOverlay). En X11 funciona razonablemente bien; en Wayland
    (Hyprland incluido) el compositor puede ignorar tanto la posicion
    fija como el 'quedate siempre arriba' -- ahi el cliente no tiene
    permiso para imponer ninguna de las dos cosas."""

    def __init__(self, position: str = "bottom-left"):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # Sin esto, un QWidget comun (a diferencia de QFrame/QPushButton)
        # IGNORA el background-color/border-radius del stylesheet y queda
        # completamente transparente -- el texto del FPS "flotando" sin
        # ningun recuadro detras, que es justo lo que se perdia contra
        # colores parecidos del juego.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._drag_pos = None
        self._position = position

        self.setStyleSheet(
            f"background-color: #000000; border-radius: 10px; "
            f"border: 1px solid {COLORS['accent']};"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        self.label = QLabel("FPS: --")
        self.label.setStyleSheet(
            f"color: {COLORS['green']}; font-size: 16px; font-weight: 800; "
            f"background: transparent; border: none;"
        )
        layout.addWidget(self.label)
        self.resize(130, 42)
        self._move_to_corner()

    def set_position(self, position: str) -> None:
        self._position = position
        self._move_to_corner()

    def _move_to_corner(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        margin = 28
        x = geo.left() + margin if "left" in self._position else geo.right() - self.width() - margin
        y = geo.top() + margin if "top" in self._position else geo.bottom() - self.height() - margin
        self.move(x, y)

    def set_fps(self, value: float) -> None:
        color = COLORS["green"]
        if value <= 0:
            color = COLORS["subtext"]
        elif value < 20:
            color = COLORS["red"]
        elif value < 45:
            color = COLORS["yellow"]
        self.label.setStyleSheet(
            f"color: {color}; font-size: 16px; font-weight: 800; "
            f"background: transparent; border: none;"
        )
        self.label.setText(f"FPS: {value:.0f}" if value > 0 else "FPS: --")

    # -- arrastrar la ventanita con el mouse ---------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        self._drag_pos = None


class FpsOverlay:
    """
    Overlay de FPS estilo BlueStacks/emulador: una placa chica, fija en
    la esquina elegida (abajo a la izquierda por defecto), garantizada
    por encima de todo -- incluida la ventana de scrcpy en pantalla
    completa.

    No es un QWidget: corre fps_overlay_helper.py como un proceso GTK3
    aparte que pide la capa "overlay" de wlr-layer-shell (el mismo
    mecanismo que usan waybar o las notificaciones), porque eso es lo
    unico que garantiza esa posicion y ese "siempre encima" en Wayland
    -- un QWidget de Qt no puede. Se le mandan los valores de FPS por
    su entrada estandar. Si gtk-layer-shell no esta instalado, se cae
    de vuelta sola a _LegacyFpsOverlay (una ventana Qt normal) para no
    dejar la funcion completamente rota.
    """

    HELPER_PATH = Path(__file__).resolve().parent / "fps_overlay_helper.py"

    def __init__(self, log=None):
        self._log = log or (lambda _msg: None)
        self._process: subprocess.Popen | None = None
        self._legacy: _LegacyFpsOverlay | None = None
        self._warned = False
        self._position = "bottom-left"

    def _start_process(self) -> bool:
        if self._process is not None and self._process.poll() is None:
            return True
        if not dependencies.gtk_layer_shell_available():
            return False
        try:
            self._process = subprocess.Popen(
                [sys.executable, str(self.HELPER_PATH), self._position],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"No se pudo iniciar el overlay de FPS: {exc}")
            self._process = None
            return False
        # El overlay GTK puede tardar un instante en arrancar, o fallar
        # casi al toque si algo del entorno grafico no coincide con lo
        # que "gtk_layer_shell_available()" pudo chequear en frio (ej.
        # sesion Wayland vs XWayland). Se verifica en 600ms sin bloquear
        # la UI -- si ya murio, se muestra el motivo real en vez de
        # quedar en silencio.
        QTimer.singleShot(600, self._check_process_alive)
        return True

    def _check_process_alive(self) -> None:
        if self._process is None or self._process.poll() is None:
            return  # sigue vivo, todo bien
        stderr_text = ""
        if self._process.stderr is not None:
            try:
                stderr_text = self._process.stderr.read().strip()
            except (OSError, ValueError):
                pass
        self._process = None
        detail = f": {stderr_text.splitlines()[-1]}" if stderr_text else ""
        self._log(
            f"El overlay de FPS (gtk-layer-shell) se cerro solo al "
            f"arrancar{detail}. Usando la ventana de respaldo."
        )
        self._use_legacy()
        self._legacy.set_position(self._position)
        self._legacy.set_fps(0)
        self._legacy.show()

    def _use_legacy(self) -> None:
        if not self._warned:
            self._warned = True
            self._log(
                "Overlay de FPS: no se encontro gtk-layer-shell instalado, "
                "se usa una ventana comun de respaldo (en Wayland/Hyprland "
                "puede no quedar fija ni 'siempre encima'). Instalalo desde "
                "la pagina 'Dependencias' para el overlay estilo BlueStacks."
            )
        if self._legacy is None:
            self._legacy = _LegacyFpsOverlay()

    def show(self, position: str | None = None) -> None:
        if position:
            self._position = position
        if self._start_process():
            self._send(f"POS:{self._position}")
            self._send("SHOW")
            return
        self._use_legacy()
        self._legacy.set_position(self._position)
        self._legacy.set_fps(0)
        self._legacy.show()

    def hide(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._send("HIDE")
        if self._legacy is not None:
            self._legacy.hide()

    def set_fps(self, value: float) -> None:
        if self._process is not None and self._process.poll() is None:
            self._send(f"{value:.1f}")
        elif self._legacy is not None:
            self._legacy.set_fps(value)

    def _send(self, line: str) -> None:
        if self._process is None or self._process.stdin is None:
            return
        try:
            self._process.stdin.write(line + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            self._process = None

    def close(self) -> None:
        """Termina el proceso auxiliar. Se llama al cerrar NexoHub para
        no dejar un proceso GTK huerfano corriendo en segundo plano."""
        if self._process is not None:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
            except OSError:
                pass
            try:
                self._process.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._process = None
        if self._legacy is not None:
            self._legacy.hide()


# --------------------------------------------------------------------------
# Pagina: Dependencias
# --------------------------------------------------------------------------
class DependenciesWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.report: dependencies.DependencyReport | None = None
        self.check_worker: DependencyCheckWorker | None = None
        self.install_worker: DependencyInstallWorker | None = None
        self.rows: dict[str, DependencyRow] = {}
        self._build_ui()
        self.on_check()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(14)

        box = QGroupBox("Requisitos del sistema")
        _apply_shadow(box)
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(10)

        hint = QLabel(
            "Se detecta el gestor de paquetes de tu distro (pacman, apt, dnf, "
            "zypper) y, si falta algo, se instala pidiendo la contrasena en "
            "una ventana grafica -- nunca en la terminal."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        box_layout.addWidget(hint)

        specs = []
        for binary in dependencies.REQUIRED_BINARIES:
            specs.append((binary, "obligatorio"))
        for binary in dependencies.OPTIONAL_BINARIES:
            detail = dependencies.OPTIONAL_BINARY_DETAILS.get(
                binary, dependencies.DEFAULT_OPTIONAL_DETAIL
            )
            specs.append((binary, detail))
        for pip_name in dependencies.PYTHON_PACKAGES.values():
            specs.append((pip_name, "paquete de Python"))

        for name, detail in specs:
            row = DependencyRow(name, detail)
            self.rows[name] = row
            box_layout.addWidget(row)

        root.addWidget(box)

        self.polkit_warning = QLabel("")
        self.polkit_warning.setWordWrap(True)
        self.polkit_warning.setStyleSheet(f"color: {COLORS['yellow']};")
        self.polkit_warning.hide()
        root.addWidget(self.polkit_warning)

        action_bar = QFrame()
        action_bar.setObjectName("actionBar")
        _apply_shadow(action_bar)
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(16, 14, 16, 14)
        actions.setSpacing(10)
        self.check_btn = QPushButton("Volver a verificar")
        self.check_btn.setObjectName("bigAction")
        self.check_btn.clicked.connect(self.on_check)
        self.install_btn = QPushButton("Instalar lo que falta")
        self.install_btn.setObjectName("primary")
        self.install_btn.setStyleSheet("padding: 12px 26px; font-size: 13px; border-radius: 8px;")
        self.install_btn.clicked.connect(self.on_install)
        actions.addWidget(self.check_btn)
        actions.addStretch(1)
        actions.addWidget(self.install_btn)
        root.addWidget(action_bar)

        log_box = QGroupBox("Registro")
        _apply_shadow(log_box)
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(100)
        log_layout.addWidget(self.log_view)
        root.addWidget(log_box)
        root.addStretch(1)

    def on_check(self) -> None:
        self.check_btn.setEnabled(False)
        self.install_btn.setEnabled(False)
        self._log("Verificando dependencias...")
        self.check_worker = DependencyCheckWorker()
        self.check_worker.done.connect(self._on_check_done)
        self.check_worker.start()

    def _on_check_done(self, report: dependencies.DependencyReport) -> None:
        self.report = report
        self.check_btn.setEnabled(True)
        self._update_rows(report)
        self.install_btn.setEnabled(not report.all_ok)
        if report.all_ok:
            self._log("Todo listo: no falta ninguna dependencia.")
        else:
            faltan = ", ".join(report.missing_binaries + report.missing_python)
            self._log(f"Falta instalar: {faltan}")

        if not report.polkit_agent_running:
            self.polkit_warning.setText(
                "⚠ No se detectó un agente de polkit corriendo. Sin uno, las "
                "ventanas de contraseña no van a aparecer. En Hyprland "
                "instala 'hyprpolkitagent' (o 'polkit-gnome') y arráncalo "
                "junto con tu sesión."
            )
            self.polkit_warning.show()
        else:
            self.polkit_warning.hide()

    def _update_rows(self, report: dependencies.DependencyReport) -> None:
        for binary in dependencies.REQUIRED_BINARIES:
            self.rows[binary].set_status(ok=binary not in report.missing_binaries)
        for binary in dependencies.OPTIONAL_BINARIES:
            missing = binary in report.missing_binaries
            self.rows[binary].set_status(ok=not missing, warn=missing)
        for pip_name in dependencies.PYTHON_PACKAGES.values():
            self.rows[pip_name].set_status(ok=pip_name not in report.missing_python)

    def on_install(self) -> None:
        if not self.report or self.report.all_ok:
            return
        commands = self.report.install_commands
        preview = "\n".join(commands) if commands else "(nada por instalar)"
        confirm = QMessageBox.question(
            self,
            "Instalar dependencias",
            "Se van a ejecutar estos comandos:\n\n"
            f"{preview}\n\n"
            "Los que necesitan root pediran la contrasena en una ventana "
            "grafica. ¿Continuar?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.install_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        self.install_worker = DependencyInstallWorker(self.report)
        self.install_worker.log.connect(self._log)
        self.install_worker.finished_ok.connect(self._on_install_done)
        self.install_worker.failed.connect(self._on_install_failed)
        self.install_worker.start()

    def _on_install_done(self) -> None:
        self._log("Instalacion completa. Verificando de nuevo...")
        self.on_check()

    def _on_install_failed(self, message: str) -> None:
        self.check_btn.setEnabled(True)
        self.install_btn.setEnabled(True)
        self._log(f"Error al instalar: {message}")
        QMessageBox.warning(self, "Error al instalar", message)

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)


# --------------------------------------------------------------------------
# Pagina: Configuracion (panel unico, sin pasos obligatorios)
# --------------------------------------------------------------------------
class ConfigWidget(QWidget):
    status_message = pyqtSignal(str)
    device_status = pyqtSignal(str, bool)  # (texto, ok)
    toast_requested = pyqtSignal(str, str)  # (mensaje, kind: success|error|info)

    def __init__(self):
        super().__init__()
        self.device = None
        self.usb = None
        self.monitor = None
        self.cpu_status = None
        self.detect_worker: DetectWorker | None = None
        self.adb_restart_worker: AdbRestartWorker | None = None
        self.launch_worker: LaunchWorker | None = None
        self.fps_overlay_window: FpsOverlay | None = None
        self.wifi_worker: WifiEnableWorker | WifiDisableWorker | None = None
        self.usb_serial: str | None = None
        self.connection_mode = "usb"  # "usb" o "wifi"
        self.audio_wifi_address: str | None = None  # canal de audio dual, aparte del principal
        self.audio_wifi_worker: WifiEnableWorker | None = None
        self.audio_launch_worker: LaunchWorker | None = None
        self.bt_worker: BluetoothCheckWorker | None = None
        self.phone_resolution_worker: PhoneResolutionWorker | None = None
        self._resolution_action: str | None = None
        self._last_audio_route = "off"
        self.profile_cards: dict[str, ProfileCard] = {}
        self.selected_profile = CUSTOM_PROFILE_KEY

        self._build_ui()
        self._load_config_into_form()

    # -- construccion de la UI -------------------------------------------------
    def _build_ui(self) -> None:
        """
        Pantalla unica y continua (sin paginas separadas por sidebar):
        arriba, siempre a la vista, el estado del celular + el perfil de
        uso; abajo de eso, el resto del detalle tecnico (Video, Audio,
        Avanzado, Comando/registro, Dependencias) como acordeones
        plegables -- se puede tener mas de uno abierto a la vez, y nada
        obliga a andar cambiando de pagina para ver diagnostico + perfil
        + un ajuste puntual al mismo tiempo.

        La barra de acciones (Generar/Guardar/Detener/Lanzar) NO se
        agrega al layout de este widget: queda en self.action_bar para
        que MainWindow la ponga fija abajo de todo, fuera del scroll,
        asi siempre esta a mano sin importar que acordeon este abierto
        o cuanto hayas scrolleado.
        """
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        self.page_home = self._build_home_page()
        self.page_device = self._build_device_page()
        self.page_video = self._build_video_page()
        self.page_audio = self._build_audio_page()
        self.page_keymap = self._build_keymap_page()
        self.page_advanced = self._build_advanced_page()
        self.page_log = self._build_log_page()
        self.dependencies_widget = DependenciesWidget()

        root.addWidget(self.page_home)
        root.addWidget(self._divider())
        root.addWidget(self.page_device)

        self.video_section = self._wrap_in_accordion("🎮", "Video y pantalla", self.page_video, expanded=True)
        self.audio_section = self._wrap_in_accordion("🔊", "Audio", self.page_audio)
        self.keymap_section = self._wrap_in_accordion("⌨", "Mapeo de teclas", self.page_keymap)
        self.advanced_section = self._wrap_in_accordion("⚙", "Avanzado", self.page_advanced)
        self.log_section = self._wrap_in_accordion("📜", "Comando y registro", self.page_log)
        self.deps_section = self._wrap_in_accordion("🧩", "Dependencias", self.dependencies_widget)
        for section in (
            self.video_section, self.audio_section, self.keymap_section,
            self.advanced_section, self.log_section, self.deps_section,
        ):
            root.addWidget(section)

        root.addStretch(1)

        # Se construye pero no se agrega a "root" a proposito (ver
        # docstring): MainWindow la toma via self.action_bar.
        self.action_bar = self._build_action_bar()

    def _wrap_in_accordion(self, icon: str, title: str, content: QWidget, expanded: bool = False) -> "CollapsibleSection":
        section = CollapsibleSection(icon, title, expanded=expanded)
        _apply_shadow(section)
        inner = QVBoxLayout(section.content)
        inner.setContentsMargins(18, 8, 18, 18)
        inner.addWidget(content)
        return section

    def _build_action_bar(self) -> QFrame:
        action_bar = QFrame()
        action_bar.setObjectName("actionBar")
        _apply_shadow(action_bar)
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(20, 14, 20, 14)
        actions.setSpacing(10)

        self.build_btn = QPushButton("Generar comando")
        self.build_btn.setToolTip(
            "Arma el comando sin lanzarlo, para revisarlo antes "
            "(se ve en 'Comando y registro')."
        )
        self.build_btn.clicked.connect(self.on_build_command)
        self.save_btn = QPushButton("Guardar en config.yaml")
        self.save_btn.setToolTip("Guarda estos valores como default para la próxima vez.")
        self.save_btn.clicked.connect(self.on_save_config)

        self.stop_btn = QPushButton("⏹  Detener transmisión")
        self.stop_btn.setObjectName("bigAction")
        self.stop_btn.setStyleSheet(f"color: {COLORS['red']}; padding: 13px 22px; font-size: 13px;")
        self.stop_btn.setToolTip("Corta scrcpy (SIGTERM) sin cerrar NexoHub.")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_stop)

        self.launch_btn = QPushButton("▶  Lanzar scrcpy")
        self.launch_btn.setObjectName("primary")
        self.launch_btn.setStyleSheet("padding: 13px 28px; font-size: 13px; border-radius: 10px;")
        _apply_shadow(self.launch_btn, blur=24, alpha=90)
        self.launch_btn.setToolTip(
            "Requiere una detección exitosa y vigente. Si detectaste con "
            "un celular y después lo cambiaste por otro, vuelve a "
            "presionar 'Detectar' en Dispositivo antes de lanzar."
        )
        self.launch_btn.setEnabled(False)
        self.launch_btn.clicked.connect(self.on_launch)

        actions.addWidget(self.build_btn)
        actions.addWidget(self.save_btn)
        actions.addStretch(1)
        actions.addWidget(self.stop_btn)
        actions.addWidget(self.launch_btn)
        return action_bar

    @staticmethod
    def _page(spacing: int = 16) -> tuple[QWidget, QVBoxLayout]:
        """Pagina base: sin caja/borde propio (la pagina completa YA es
        la unidad visual, a diferencia de cuando varias convivian en un
        solo scroll y necesitaban una tarjeta cada una para separarse)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        return page, layout

    # -- inicio: perfil + accion principal -------------------------------------
    def _build_home_page(self) -> QWidget:
        page, layout = self._page(18)

        hint = QLabel(
            "Elige un perfil: ajusta automáticamente codec, bitrate y "
            "resolución, y luego lanza scrcpy. El detalle fino de cada "
            "cosa vive en los apartados de abajo (Video, Audio, Avanzado)."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        for key, icon, title, desc, tooltip, tint in PROFILE_DEFS:
            card = ProfileCard(key, icon, title, desc, tooltip, tint)
            card.clicked.connect(self.on_profile_card_clicked)
            self.profile_cards[key] = card
            cards_row.addWidget(card)
        layout.addLayout(cards_row)

        self.launch_summary_label = QLabel("")
        self.launch_summary_label.setWordWrap(True)
        self.launch_summary_label.setProperty("role", "hint")
        layout.addWidget(self.launch_summary_label)

        self.stream_warning = QLabel("")
        self.stream_warning.setWordWrap(True)
        self.stream_warning.setStyleSheet(f"color: {COLORS['yellow']};")
        self.stream_warning.hide()
        layout.addWidget(self.stream_warning)

        return page

    # -- dispositivo --------------------------------------------------------
    def _build_device_page(self) -> QWidget:
        page, layout = self._page(16)

        hint = QLabel(
            "Conecta el celular por USB con la depuración USB activada y "
            "presiona \"Detectar\". Si es la primera vez, el celular te "
            "pedirá que aceptes el permiso de depuración en pantalla."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.connect_status_label = QLabel("Sin conectar todavía")
        self.connect_status_label.setStyleSheet(
            f"color: {COLORS['subtext']}; font-size: 15px; font-weight: 700;"
        )
        layout.addWidget(self.connect_status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.detect_btn = QPushButton("Detectar")
        self.detect_btn.setObjectName("primary")
        self.detect_btn.setStyleSheet("padding: 13px 28px; font-size: 13px; border-radius: 10px;")
        _apply_shadow(self.detect_btn, blur=22, alpha=80)
        self.detect_btn.setToolTip("Lee el celular por ADB, la topología USB, el monitor y el CPU.")
        self.detect_btn.clicked.connect(self.on_detect)
        btn_row.addWidget(self.detect_btn)
        self.wifi_btn = QPushButton("Conectar por WiFi")
        self.wifi_btn.setToolTip(
            "Cambia el celular a modo WiFi/LAN para usar scrcpy sin cable. "
            "El celular y el PC deben estar en la misma red. Primero hay "
            "que detectar por USB al menos una vez."
        )
        self.wifi_btn.setEnabled(False)
        self.wifi_btn.clicked.connect(self.on_toggle_wifi)
        btn_row.addWidget(self.wifi_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.adb_restart_btn = QPushButton("¿Problemas? Reiniciar ADB")
        self.adb_restart_btn.setFlat(True)
        self.adb_restart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.adb_restart_btn.setStyleSheet(
            f"background-color: transparent; color: {COLORS['subtext']}; "
            f"font-weight: 600; padding: 4px 0; text-align: left;"
        )
        self.adb_restart_btn.setToolTip(
            "Mata y vuelve a levantar el servidor adb (adb kill-server + "
            "adb start-server). Úsalo si el celular queda marcado "
            "'unauthorized'/'offline' sin motivo, si cambiaste de celular "
            "y quedó un estado colgado, o si 'Detectar' no refleja lo que "
            "hay conectado ahora mismo."
        )
        self.adb_restart_btn.clicked.connect(self.on_restart_adb)
        layout.addWidget(self.adb_restart_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self.diag_section = CollapsibleSection("", "Detalles técnicos del dispositivo", expanded=False)
        _apply_shadow(self.diag_section)
        diag_inner = QVBoxLayout(self.diag_section.content)
        diag_inner.setContentsMargins(16, 6, 16, 16)
        self.diag_grid = QGridLayout()
        self.diag_grid.setSpacing(12)
        diag_fields = [
            "Conexión", "Celular", "Chipset", "Mejor encoder",
            "Velocidad USB", "USB autosuspend", "Monitor", "CPU governor",
        ]
        self.diag_cards: dict[str, DiagCard] = {}
        columns = 3
        for i, field_name in enumerate(diag_fields):
            diag_card = DiagCard(field_name)
            self.diag_cards[field_name] = diag_card
            self.diag_grid.addWidget(diag_card, i // columns, i % columns)
        for col in range(columns):
            self.diag_grid.setColumnStretch(col, 1)
        diag_inner.addLayout(self.diag_grid)
        layout.addWidget(self.diag_section)

        self.phone_resolution_section = self._build_phone_resolution_section()
        layout.addWidget(self.phone_resolution_section)
        layout.addStretch(1)

        return page

    # -- resolucion del celular por ADB (independiente de la de transmision) --
    def _build_phone_resolution_section(self) -> QWidget:
        section = CollapsibleSection("", "Resolución del celular (ADB)", expanded=False)
        _apply_shadow(section)
        layout = QVBoxLayout(section.content)
        layout.setContentsMargins(17, 6, 17, 17)
        layout.setSpacing(12)

        hint = QLabel(
            "Esto cambia la resolución REAL de la pantalla del celular "
            "(vía ADB), no la de video en Video. Sirve para igualar el "
            "celular a tu monitor (ej. monitor 1920x1080 → celular "
            "1080x1920) para que el juego ya renderice al tamaño final. "
            "Algunos juegos y lanzadores pueden verse raros con una "
            "resolución no nativa; \"Restablecer\" vuelve a la de fábrica "
            "en cualquier momento."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        self.phone_resolution_status = QLabel("Detecta el celular para ver su resolución.")
        self.phone_resolution_status.setProperty("role", "hint")
        self.phone_resolution_status.setWordWrap(True)
        layout.addWidget(self.phone_resolution_status)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_row.addWidget(QLabel("Ancho"))
        self.res_width_spin = QSpinBox()
        self.res_width_spin.setRange(240, 7680)
        self.res_width_spin.setValue(1080)
        size_row.addWidget(self.res_width_spin)
        size_row.addWidget(QLabel("×"))
        self.res_height_spin = QSpinBox()
        self.res_height_spin.setRange(240, 7680)
        self.res_height_spin.setValue(1920)
        size_row.addWidget(self.res_height_spin)
        size_row.addStretch(1)
        layout.addLayout(size_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.res_use_monitor_btn = QPushButton("Usar la de mi monitor")
        self.res_use_monitor_btn.setToolTip(
            "Completa Ancho/Alto invirtiendo la resolución del monitor "
            "detectado (monitor horizontal → celular vertical). No aplica "
            "nada todavía, solo llena los campos."
        )
        self.res_use_monitor_btn.clicked.connect(self.on_resolution_use_monitor)
        self.res_apply_btn = QPushButton("Aplicar")
        self.res_apply_btn.setToolTip("Corre 'adb shell wm size ANCHOxALTO' en el celular.")
        self.res_apply_btn.setEnabled(False)
        self.res_apply_btn.clicked.connect(self.on_resolution_apply)
        self.res_reset_btn = QPushButton("Restablecer a nativa")
        self.res_reset_btn.setToolTip("Corre 'adb shell wm size reset': vuelve a la resolución de fábrica.")
        self.res_reset_btn.setEnabled(False)
        self.res_reset_btn.clicked.connect(self.on_resolution_reset)
        self.res_refresh_btn = QPushButton("Leer actual")
        self.res_refresh_btn.setToolTip("Vuelve a leer la resolución actual del celular por ADB.")
        self.res_refresh_btn.setEnabled(False)
        self.res_refresh_btn.clicked.connect(self.on_resolution_refresh)
        btn_row.addWidget(self.res_use_monitor_btn)
        btn_row.addWidget(self.res_refresh_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.res_reset_btn)
        btn_row.addWidget(self.res_apply_btn)
        layout.addLayout(btn_row)

        return section

    # -- video: resolucion, codec, bitrate, fps --------------------------------
    def _build_video_page(self) -> QWidget:
        page, layout = self._page(14)

        hint = QLabel(
            "El perfil de uso (en Inicio) ya deja esto en buenos valores "
            "por defecto -- tócalo solo si sabes lo que estás cambiando."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addWidget(self._section_header("Resolución y codec"))
        form_box = QWidget()
        advanced_layout = QFormLayout(form_box)
        advanced_layout.setContentsMargins(0, 2, 0, 0)
        advanced_layout.setSpacing(10)
        advanced_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)

        self.codec_combo = QComboBox()
        self.codec_combo.addItems(["auto", "h264", "h265", "av1"])
        self.codec_combo.setToolTip(
            "Formato de compresión de video. 'auto' deja que se elija el "
            "que mejor soporte tenga en el celular. h264 suele dar menor "
            "latencia; h265/av1 mejor calidad por bit."
        )
        advanced_layout.addRow("Video codec", self.codec_combo)

        self.encoder_edit = QLineEdit()
        self.encoder_edit.setPlaceholderText("vacío = detectar el mejor (recomendado)")
        self.encoder_edit.setToolTip(ENCODER_HELP)
        advanced_layout.addRow(
            self._label_with_help("Video encoder", "Video encoder", ENCODER_HELP),
            self.encoder_edit,
        )

        self.bitrate_edit = QLineEdit()
        self.bitrate_edit.setPlaceholderText("ej. 8M, 16M, 32M (vacío = auto)")
        self.bitrate_edit.setToolTip(
            "Bitrate máximo de video. Más alto = mejor calidad pero más "
            "ancho de banda USB. Si lo dejas vacío se ajusta según la "
            "velocidad USB detectada."
        )
        advanced_layout.addRow("Bitrate max", self.bitrate_edit)
        self.bitrate_edit.textChanged.connect(self._check_stream_thresholds)

        self.max_size_spin = QSpinBox()
        self.max_size_spin.setRange(0, 4096)
        self.max_size_spin.setSpecialValueText("auto")
        self.max_size_spin.setToolTip(
            "Lado más largo del video en píxeles. 'auto' ya usa la "
            "resolución de tu monitor destino -- este campo es para "
            "ajustarla a gusto (por ej. bajarla un poco para ganar fps)."
        )
        max_size_row = QHBoxLayout()
        max_size_row.setSpacing(8)
        max_size_row.addWidget(self.max_size_spin)
        self.match_monitor_btn = QPushButton("Usar resolución de mi monitor")
        self.match_monitor_btn.setToolTip(
            "Pone el 'Max size' en el lado más largo de tu monitor principal, "
            "para que la transmisión llene toda la pantalla sin recortes ni "
            "franjas negras al ponerla en pantalla completa."
        )
        self.match_monitor_btn.clicked.connect(self.on_match_monitor_resolution)
        max_size_row.addWidget(self.match_monitor_btn)
        max_size_row.addStretch(1)
        advanced_layout.addRow("Max size (px)", max_size_row)

        self.fullscreen_check = QCheckBox("Iniciar scrcpy en pantalla completa")
        self.fullscreen_check.setToolTip(
            "Abre la ventana de scrcpy ya maximizada a pantalla completa "
            "(--fullscreen) en vez de en una ventana. Combinalo con "
            "'Usar resolución de mi monitor' de arriba para que se vea nítido."
        )
        advanced_layout.addRow("", self.fullscreen_check)
        layout.addWidget(form_box)
        layout.addWidget(self._divider())

        layout.addWidget(self._section_header("FPS y overlay"))
        fps_row = QHBoxLayout()
        fps_row.setSpacing(8)
        fps_row.addWidget(QLabel("FPS máximo"))
        self.max_fps_spin = QSpinBox()
        self.max_fps_spin.setRange(0, 240)
        self.max_fps_spin.setSpecialValueText("sin límite")
        self.max_fps_spin.setToolTip(
            "Limita el frame rate de captura en el celular (--max-fps). "
            "0 = sin límite, usa lo que entregue la pantalla."
        )
        fps_row.addWidget(self.max_fps_spin)
        fps_row.addStretch(1)
        layout.addLayout(fps_row)

        overlay_row = QHBoxLayout()
        overlay_row.setSpacing(8)
        self.fps_overlay_check = QCheckBox("Mostrar overlay de FPS en pantalla")
        self.fps_overlay_check.setToolTip(
            "Abre una ventanita flotante, siempre encima de la transmisión, "
            "con el FPS real en vivo. Se cierra sola al detener o cerrar scrcpy."
        )
        overlay_row.addWidget(self.fps_overlay_check)
        self.fps_position_combo = QComboBox()
        self.fps_position_combo.addItem("Abajo a la izquierda", "bottom-left")
        self.fps_position_combo.addItem("Abajo a la derecha", "bottom-right")
        self.fps_position_combo.addItem("Arriba a la izquierda", "top-left")
        self.fps_position_combo.addItem("Arriba a la derecha", "top-right")
        self.fps_position_combo.setToolTip(
            "En que esquina de la pantalla aparece el overlay de FPS."
        )
        overlay_row.addWidget(self.fps_position_combo)
        overlay_row.addStretch(1)
        layout.addLayout(overlay_row)

        # Cualquier edicion manual en Video pasa el perfil a "Personalizado"
        for widget in (self.codec_combo, self.encoder_edit, self.bitrate_edit, self.max_size_spin):
            if isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self._on_advanced_edited)
            elif isinstance(widget, QLineEdit):
                widget.textEdited.connect(self._on_advanced_edited)
            elif isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._on_advanced_edited)
        self.fullscreen_check.toggled.connect(self._on_advanced_edited)

        layout.addStretch(1)
        return page

    # -- audio ------------------------------------------------------------------
    def _build_audio_page(self) -> QWidget:
        page, layout = self._page(14)

        audio_row = QHBoxLayout()
        audio_row.setSpacing(8)
        audio_row.addWidget(QLabel("Audio"))
        self.audio_route_combo = QComboBox()
        self.audio_route_combo.addItem("Apagado (sin audio)", "off")
        self.audio_route_combo.addItem("Por USB, junto con video", "scrcpy")
        self.audio_route_combo.addItem("Bluetooth (externo, recomendado)", "bluetooth")
        self.audio_route_combo.addItem("WiFi en paralelo (dual, menor lag)", "wifi_dual")
        self.audio_route_combo.setToolTip(
            "USB: el audio comparte el mismo túnel que video/control (más "
            "simple, puede sumar lag en USB 2.0). Bluetooth: el celular le "
            "manda el audio al PC directo por Bluetooth, sin tocar scrcpy "
            "(cero impacto en video/input, pero ~100-150ms de desfasaje "
            "típico de A2DP). WiFi (dual): una segunda instancia de scrcpy, "
            "solo audio, corre en paralelo por WiFi mientras video/control "
            "siguen por USB -- necesita Android 11+."
        )
        audio_row.addWidget(self.audio_route_combo, 1)
        self.bt_status_btn = QPushButton("Bluetooth")
        self.bt_status_btn.setToolTip(
            "Revisa si el PC tiene Bluetooth y qué dispositivos de audio "
            "ya tiene emparejados/conectados. El emparejamiento en sí se "
            "hace desde el applet de Bluetooth del sistema, no desde aquí."
        )
        self.bt_status_btn.clicked.connect(self.on_check_bluetooth)
        audio_row.addWidget(self.bt_status_btn)
        layout.addLayout(audio_row)
        layout.addWidget(self._divider())

        layout.addWidget(self._section_header("Ajustes finos"))
        audio_box = QWidget()
        audio_form = QFormLayout(audio_box)
        audio_form.setContentsMargins(0, 2, 0, 0)
        audio_form.setSpacing(10)
        self.audio_codec_combo = QComboBox()
        self.audio_codec_combo.addItems(["auto", "opus", "aac", "flac", "raw"])
        self.audio_codec_combo.setToolTip(
            "Codec de audio de scrcpy (default: opus). En varios chipsets "
            "'aac' tiene encoder de hardware, mientras que opus suele ser "
            "software: si el audio mete lag en competitivo, probar aac "
            "primero."
        )
        audio_form.addRow("Audio codec", self.audio_codec_combo)

        self.audio_bitrate_edit = QLineEdit()
        self.audio_bitrate_edit.setPlaceholderText("ej. 64K, 128K (vacío = default 128K)")
        self.audio_bitrate_edit.setToolTip(
            "Bitrate de audio. Bajarlo (ej. 64K) reduce lo que compite con "
            "video+control en el mismo túnel USB/ADB."
        )
        audio_form.addRow("Audio bitrate", self.audio_bitrate_edit)

        self.audio_buffer_spin = QSpinBox()
        self.audio_buffer_spin.setRange(0, 500)
        self.audio_buffer_spin.setValue(50)
        self.audio_buffer_spin.setSuffix(" ms")
        self.audio_buffer_spin.setToolTip(
            "Buffer de audio (default scrcpy: 50ms). Bajarlo reduce el "
            "delay de audio, pero si baja demasiado pueden aparecer cortes "
            "o \"glitches\". Ir probando de a poco (ej. 50 -> 30 -> 15)."
        )
        audio_form.addRow("Audio buffer", self.audio_buffer_spin)
        self.audio_route_combo.currentIndexChanged.connect(self._on_audio_route_changed)
        layout.addWidget(audio_box)

        for widget in (self.audio_codec_combo, self.audio_bitrate_edit, self.audio_buffer_spin):
            if isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self._on_advanced_edited)
            elif isinstance(widget, QLineEdit):
                widget.textEdited.connect(self._on_advanced_edited)
            elif isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._on_advanced_edited)

        layout.addStretch(1)
        return page

    # -- mapeo de teclas: todavia no implementado, solo deja la intencion --------
    def _build_keymap_page(self) -> QWidget:
        """
        Placeholder a proposito: todavia no hay mapeador de teclas, pero
        se dejó el espacio reservado para que se note para dónde va la
        app (poder asignar teclas/botones del mouse a zonas táctiles de
        la pantalla, como hacen los emuladores de Android en PC) en vez
        de que aparezca de la nada el día que se implemente.
        """
        page, layout = self._page(14)

        banner = QFrame()
        banner.setObjectName("comingSoonBanner")
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(16, 14, 16, 14)
        banner_layout.setSpacing(12)
        badge = QLabel("🚧 Próximamente")
        badge.setProperty("role", "pill")
        badge.setStyleSheet(
            f"background-color: {COLORS['accent_soft']}; color: {COLORS['accent']}; "
            f"border: 1px solid {COLORS['accent_border']}; font-weight: 700;"
        )
        banner_layout.addWidget(badge)
        banner_text = QLabel(
            "Todavía no está disponible. La idea es poder asignar teclas del "
            "teclado y botones del mouse a zonas táctiles de la pantalla del "
            "celular, para jugar con teclado/mouse juegos pensados para touch "
            "-- como hacen los emuladores de Android en PC."
        )
        banner_text.setWordWrap(True)
        banner_text.setStyleSheet(f"color: {COLORS['subtext']}; font-size: 12px;")
        banner_layout.addWidget(banner_text, 1)
        layout.addWidget(banner)

        preview_hint = QLabel("Vista previa de cómo se va a ver (todavía no es funcional):")
        preview_hint.setProperty("role", "hint")
        layout.addWidget(preview_hint)

        mock = QFrame()
        mock.setObjectName("keymapMock")
        mock_layout = QHBoxLayout(mock)
        mock_layout.setContentsMargins(16, 16, 16, 16)
        mock_layout.setSpacing(10)
        mock_label = QLabel("Sin mapeos todavía")
        mock_label.setStyleSheet(f"color: {COLORS['subtext']}; font-size: 12px;")
        mock_layout.addWidget(mock_label)
        mock_layout.addStretch(1)
        add_key_btn = QPushButton("+  Agregar mapeo de tecla")
        add_key_btn.setEnabled(False)
        add_key_btn.setToolTip("Todavía no implementado -- esto es solo un adelanto de cómo se va a ver.")
        mock_layout.addWidget(add_key_btn)
        layout.addWidget(mock)

        layout.addStretch(1)
        return page

    # -- avanzado: conexion, flags, fixes del sistema ----------------------------
    def _build_advanced_page(self) -> QWidget:
        page, layout = self._page(14)

        hint = QLabel(
            "Conexión, flags manuales de scrcpy y ajustes del sistema que "
            "casi nunca hace falta tocar."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        layout.addWidget(self._section_header("Conexión y flags"))
        conn_box = QWidget()
        conn_form = QFormLayout(conn_box)
        conn_form.setContentsMargins(0, 2, 0, 0)
        conn_form.setSpacing(10)

        self.input_mode_combo = QComboBox()
        self.input_mode_combo.addItems(["uhid", "sdk", "aoa", "disabled"])
        self.input_mode_combo.setToolTip(INPUT_MODE_HELP)
        conn_form.addRow(
            self._label_with_help("Modo de entrada", "Modo de entrada", INPUT_MODE_HELP),
            self.input_mode_combo,
        )

        self.wifi_port_spin = QSpinBox()
        self.wifi_port_spin.setRange(1024, 65535)
        self.wifi_port_spin.setValue(wireless.DEFAULT_PORT)
        self.wifi_port_spin.setToolTip(
            "Puerto TCP que usa ADB para la conexión WiFi/LAN. El default "
            "(5555) funciona en casi todos los casos; cámbialo solo si ese "
            "puerto está ocupado o bloqueado en tu red."
        )
        conn_form.addRow("Puerto WiFi (ADB)", self.wifi_port_spin)

        self.extra_flags_edit = QLineEdit()
        self.extra_flags_edit.setPlaceholderText(
            "flags extra de scrcpy, ej: --video-codec-options=i-frame-interval=1"
        )
        self.extra_flags_edit.setToolTip(
            "Cualquier flag adicional de scrcpy separado por espacios, tal "
            "cual se le pasaría en la terminal."
        )
        conn_form.addRow("Flags extra", self.extra_flags_edit)
        layout.addWidget(conn_box)
        layout.addWidget(self._divider())

        layout.addWidget(self._section_header("Ajustes del sistema"))
        fixes_box = QWidget()
        fixes_layout = QVBoxLayout(fixes_box)
        fixes_layout.setContentsMargins(0, 2, 0, 0)
        fixes_layout.setSpacing(8)
        self.fix_usb_check = QCheckBox("Forzar USB power a 'on'")
        self.fix_usb_check.setToolTip(
            "Evita que el sistema suspenda el puerto USB del celular "
            "(autosuspend), lo que puede causar desconexiones o caídas "
            "de fps. Pide contraseña gráfica al lanzar (una sola vez, "
            "aunque haya varios hubs USB de por medio)."
        )
        self.fix_cpu_check = QCheckBox("Forzar CPU 'performance'")
        self.fix_cpu_check.setToolTip(
            "Cambia el governor de CPU del PC a 'performance' para evitar "
            "bajones de rendimiento por ahorro de energía. Si también "
            "activaste el USB fix, se pide todo con una sola contraseña."
        )
        fixes_layout.addWidget(self.fix_usb_check)
        fixes_layout.addWidget(self.fix_cpu_check)
        layout.addWidget(fixes_box)
        layout.addWidget(self._divider())

        for widget in (self.extra_flags_edit,):
            widget.textEdited.connect(self._on_advanced_edited)
        self.input_mode_combo.currentTextChanged.connect(self._on_advanced_edited)

        layout.addStretch(1)
        return page

    # -- comando y registro -------------------------------------------------
    def _build_log_page(self) -> QWidget:
        page, layout = self._page(12)

        hint = QLabel("Comando armado y salida en vivo de scrcpy.")
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        terminal = QFrame()
        terminal.setObjectName("terminal")
        terminal_layout = QVBoxLayout(terminal)
        terminal_layout.setContentsMargins(16, 14, 16, 16)
        terminal_layout.setSpacing(10)

        self.command_view = QPlainTextEdit()
        self.command_view.setObjectName("terminalView")
        self.command_view.setReadOnly(True)
        self.command_view.setMaximumHeight(64)
        self.command_view.setPlaceholderText(
            "$ presiona \"Generar comando\" (en Avanzado) o \"Lanzar scrcpy\" "
            "para verlo aquí..."
        )
        terminal_layout.addWidget(self.command_view)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("terminalView")
        self.log_view.setReadOnly(True)
        terminal_layout.addWidget(self.log_view)
        layout.addWidget(terminal, 1)

        return page


    def _update_launch_summary(self) -> None:
        if not self.device:
            self.launch_summary_label.setText("")
            return
        profile_names = {key: title for key, _, title, _, _, _ in PROFILE_DEFS}
        profile_text = profile_names.get(self.selected_profile, "Personalizado")
        self.launch_summary_label.setText(
            f"{self.device.brand} {self.device.model}   ·   perfil: {profile_text}"
        )

    def _label_with_help(self, text: str, title: str, help_text: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel(text))
        layout.addWidget(_help_button(title, help_text, container))
        return container

    @staticmethod
    def _section_header(text: str) -> QLabel:
        """Encabezado liviano para subdividir una seccion plegable sin
        meter otra caja-con-borde adentro -- evita el efecto 'caja
        dentro de caja' de anidar QGroupBox dentro de CollapsibleSection."""
        label = QLabel(text.upper())
        label.setStyleSheet(
            f"color: {COLORS['subtext']}; font-size: 11px; font-weight: 700; "
            f"letter-spacing: 0.6px; background: transparent;"
        )
        return label

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"background-color: {COLORS['surface0']}; max-height: 1px; border: none;")
        return line

    # -- seleccion de perfil ----------------------------------------------------
    def on_profile_card_clicked(self, key: str) -> None:
        self.selected_profile = key
        for card_key, card in self.profile_cards.items():
            card.set_selected(card_key == key)
        if key != CUSTOM_PROFILE_KEY:
            self._apply_profile_overlay(key)
        self._update_launch_summary()

    def _apply_profile_overlay(self, key: str) -> None:
        profiles = self.config.get("profiles", {})
        overlay = profiles.get(key)
        if not overlay:
            return
        # Bloqueamos senales para no disparar _on_advanced_edited (que
        # volveria a marcar "Personalizado") mientras aplicamos el preset.
        widgets = (self.codec_combo, self.encoder_edit, self.bitrate_edit,
                   self.max_size_spin, self.extra_flags_edit, self.input_mode_combo,
                   self.audio_route_combo)
        for widget in widgets:
            widget.blockSignals(True)
        try:
            # El encoder vacio es intencional: cada perfil se re-resuelve
            # segun el chipset del celular detectado, nunca por nombre fijo.
            self.encoder_edit.setText(overlay.get("video_encoder", ""))
            self.codec_combo.setCurrentText(overlay.get("video_codec", "auto"))
            self.bitrate_edit.setText(overlay.get("bitrate_max", ""))
            self.max_size_spin.setValue(overlay.get("max_size", 0))
            if "input_mode" in overlay:
                self.input_mode_combo.setCurrentText(overlay["input_mode"])
            if "audio" in overlay:
                self._set_audio_route_combo("scrcpy" if overlay["audio"] else "off")
            self.extra_flags_edit.setText(" ".join(overlay.get("extra_flags", [])))
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self._update_audio_advanced_enabled(self.audio_route_combo.currentData())
        self._last_audio_route = self.audio_route_combo.currentData()
        self._log(f"Perfil '{key}' aplicado.")

    # -- avisos de FPS / bitrate alto ------------------------------------------
    def _check_stream_thresholds(self, *_args) -> None:
        """Si el usuario pide FPS o bitrate por encima de lo razonable
        para USB 2.0/WiFi, muestra un aviso: la calidad mejora, pero el
        delay o la fluidez de la transmision pueden resentirse."""
        reasons = []
        fps_value = self.max_fps_spin.value()
        if fps_value and fps_value > FPS_WARN_THRESHOLD:
            reasons.append(f"{fps_value} FPS")
        bitrate_mbps = _parse_bitrate_mbps(self.bitrate_edit.text())
        if bitrate_mbps is not None and bitrate_mbps > BITRATE_WARN_THRESHOLD_MBPS:
            reasons.append(f"{bitrate_mbps:.0f} Mbps de bitrate")

        if reasons:
            self.stream_warning.setText(
                "⚠ " + " y ".join(reasons) + ": la calidad de imagen va a "
                "mejorar, pero el delay o la fluidez de la transmision "
                "pueden verse afectados segun tu USB/WiFi y el celular."
            )
            self.stream_warning.show()
        else:
            self.stream_warning.hide()

    def _set_audio_route_combo(self, route: str) -> None:
        idx = self.audio_route_combo.findData(route)
        if idx >= 0:
            self.audio_route_combo.setCurrentIndex(idx)

    def _update_audio_advanced_enabled(self, route: str) -> None:
        """Los ajustes finos de audio (codec/bitrate/buffer) solo aplican
        si hay audio de scrcpy corriendo en algun lado: en el canal
        principal ('scrcpy') o en el sidecar de WiFi ('wifi_dual'). Con
        'off' o 'bluetooth' no hay ningun --audio-* que afinar."""
        enabled = route in ("scrcpy", "wifi_dual")
        for widget in (self.audio_codec_combo, self.audio_bitrate_edit, self.audio_buffer_spin):
            widget.setEnabled(enabled)

    def _revert_audio_route(self) -> None:
        self.audio_route_combo.blockSignals(True)
        self._set_audio_route_combo(self._last_audio_route)
        self.audio_route_combo.blockSignals(False)
        self._update_audio_advanced_enabled(self._last_audio_route)

    def _on_audio_route_changed(self, _index=None) -> None:
        route = self.audio_route_combo.currentData()
        self._update_audio_advanced_enabled(route)

        if route == "wifi_dual":
            if self.connection_mode == "wifi":
                QMessageBox.information(
                    self, "WiFi (dual) no aplica",
                    "El video/control ya estan yendo por WiFi, asi que una "
                    "segunda instancia de audio por WiFi no separa nada "
                    "(seria el mismo canal de radio). Esta ruta sirve "
                    "cuando el video va por USB y solo el audio se manda "
                    "aparte por WiFi."
                )
                self._revert_audio_route()
                return
            if not self.usb_serial:
                QMessageBox.information(
                    self, "Falta detectar",
                    "Primero pulsa 'Detectar' con el celular "
                    "conectado por USB."
                )
                self._revert_audio_route()
                return
            if self.audio_wifi_address:
                self._last_audio_route = route
                return
            self._log("Preparando canal de audio por WiFi (dual)...")
            self.status_message.emit("Preparando audio por WiFi...")
            self.audio_route_combo.setEnabled(False)
            port = self.wifi_port_spin.value()
            self.audio_wifi_worker = WifiEnableWorker(self.usb_serial, port)
            self.audio_wifi_worker.done.connect(self._on_audio_wifi_ready)
            self.audio_wifi_worker.failed.connect(self._on_audio_wifi_failed)
            self.audio_wifi_worker.start()
            return  # _last_audio_route se fija cuando el worker termine

        if route == "bluetooth":
            self._log(
                "Audio por Bluetooth: emparéjalo desde Android (Ajustes > "
                "Bluetooth) eligiendo el PC como salida de audio. Pulsa el "
                "botón 'Bluetooth' para ver el estado del adaptador del PC."
            )

        self._last_audio_route = route

    def _on_audio_wifi_ready(self, address: str) -> None:
        self.audio_wifi_address = address
        self.audio_route_combo.setEnabled(True)
        self._last_audio_route = "wifi_dual"
        self._log(f"Canal de audio por WiFi listo en {address}.")
        self.status_message.emit("Audio por WiFi listo")

    def _on_audio_wifi_failed(self, message: str) -> None:
        self.audio_route_combo.setEnabled(True)
        self._log(f"No se pudo preparar el audio por WiFi: {message}")
        self._revert_audio_route()
        QMessageBox.warning(self, "Audio por WiFi (dual)", message)

    def on_match_monitor_resolution(self) -> None:
        if not self.monitor:
            QMessageBox.information(
                self, "Falta detectar",
                "Primero pulsa 'Detectar' en el paso 1 para leer la "
                "resolución de tu monitor."
            )
            return
        self.max_size_spin.setValue(self.monitor.long_side)
        self._log(
            f"Max size ajustado a {self.monitor.long_side}px "
            f"(resolución de {self.monitor.name}: "
            f"{self.monitor.width}x{self.monitor.height})."
        )

    def on_check_bluetooth(self) -> None:
        self.bt_status_btn.setEnabled(False)
        self._log("Revisando Bluetooth del PC...")
        self.bt_worker = BluetoothCheckWorker()
        self.bt_worker.done.connect(self._on_bluetooth_checked)
        self.bt_worker.failed.connect(self._on_bluetooth_failed)
        self.bt_worker.start()

    def _on_bluetooth_checked(self, status) -> None:
        self.bt_status_btn.setEnabled(True)
        if not status.available:
            QMessageBox.information(
                self, "Bluetooth",
                "No se detecto ningun adaptador Bluetooth en el PC. Si el "
                "hardware no tiene uno integrado, un dongle USB Bluetooth "
                "anda bien y son baratos."
            )
            return
        lines = [f"Adaptador: {'encendido' if status.powered else 'apagado'}."]
        if not status.powered:
            lines.append("Prendelo desde el applet de Bluetooth del sistema.")
        if status.connected_devices:
            lines.append("Conectado ahora: " + ", ".join(status.connected_devices))
        elif status.connected_unknown and status.paired_devices:
            lines.append("Emparejados: " + ", ".join(status.paired_devices))
            lines.append("(no se pudo saber cuales estan conectados ahora mismo)")
        elif status.paired_devices:
            lines.append("Emparejados (ninguno conectado ahora): " + ", ".join(status.paired_devices))
        else:
            lines.append("No hay ningun dispositivo emparejado todavia.")
        lines.append("")
        lines.append(
            "Para usar el PC como salida de audio del juego: emparéjalo "
            "desde Ajustes > Bluetooth en Android, y elígelo ahí como "
            "dispositivo de audio (igual que unos parlantes Bluetooth)."
        )
        self._log("Bluetooth: " + " ".join(lines[:2]))
        QMessageBox.information(self, "Estado de Bluetooth", "\n".join(lines))

    def _on_bluetooth_failed(self, message: str) -> None:
        self.bt_status_btn.setEnabled(True)
        self._log(f"No se pudo revisar Bluetooth: {message}")
        QMessageBox.warning(self, "Bluetooth", message)

    # -- resolucion del celular por ADB ----------------------------------------
    def on_resolution_use_monitor(self) -> None:
        if self.monitor is None:
            QMessageBox.information(
                self, "Falta detectar",
                "Detecta el celular primero para conocer el monitor destino."
            )
            return
        width, height = resolution.size_for_monitor(self.monitor.width, self.monitor.height)
        self.res_width_spin.setValue(width)
        self.res_height_spin.setValue(height)
        self._log(
            f"Sugerido {width}x{height} a partir del monitor "
            f"{self.monitor.width}x{self.monitor.height}. Revisa y presiona "
            f"'Aplicar' para mandarlo al celular."
        )

    def on_resolution_refresh(self) -> None:
        self._run_resolution_worker("get")

    def on_resolution_apply(self) -> None:
        self._run_resolution_worker(
            "set", self.res_width_spin.value(), self.res_height_spin.value()
        )

    def on_resolution_reset(self) -> None:
        self._run_resolution_worker("reset")

    def _run_resolution_worker(self, action: str, width: int = 0, height: int = 0) -> None:
        if self.device is None:
            QMessageBox.information(
                self, "Falta detectar", "Primero presiona 'Detectar' con el celular conectado."
            )
            return
        for btn in (self.res_use_monitor_btn, self.res_apply_btn, self.res_reset_btn, self.res_refresh_btn):
            btn.setEnabled(False)
        self._resolution_action = action
        if action == "set":
            self._log(f"Cambiando resolución del celular a {width}x{height}...")
        elif action == "reset":
            self._log("Restableciendo resolución del celular a la nativa...")
        self.phone_resolution_worker = PhoneResolutionWorker(
            self.device.serial, action, width, height
        )
        self.phone_resolution_worker.done.connect(self._on_resolution_worker_done)
        self.phone_resolution_worker.failed.connect(self._on_resolution_worker_failed)
        self.phone_resolution_worker.start()

    def _on_resolution_worker_done(self, size) -> None:
        for btn in (self.res_use_monitor_btn, self.res_apply_btn, self.res_reset_btn, self.res_refresh_btn):
            btn.setEnabled(True)
        estado = "forzada" if size.is_override else "nativa"
        self.phone_resolution_status.setText(f"Resolución actual del celular: {size} ({estado}).")
        self._log(f"Resolución del celular: {size} ({estado}).")
        # El refresco silencioso ("get", ej. al abrir la pagina) no merece
        # una notificacion -- nadie pidio nada. "set"/"reset" si, porque
        # ahi el usuario acaba de pedir un cambio y quiere confirmacion
        # de que se aplico, sin tener que ir a mirar la pestana Registro.
        if self._resolution_action == "set":
            self._toast(f"Resolución aplicada: {size} ✓", kind="success")
        elif self._resolution_action == "reset":
            self._toast(f"Resolución restablecida a la nativa: {size} ✓", kind="success")
        self._resolution_action = None

    def _on_resolution_worker_failed(self, message: str) -> None:
        for btn in (self.res_use_monitor_btn, self.res_apply_btn, self.res_reset_btn, self.res_refresh_btn):
            btn.setEnabled(True)
        self._log(f"Error de resolución del celular: {message}")
        if self._resolution_action in ("set", "reset"):
            self._toast(f"No se pudo cambiar la resolución: {message}", kind="error")
        else:
            QMessageBox.warning(self, "Resolución del celular", message)
        self._resolution_action = None

    def _on_advanced_edited(self, *_args) -> None:
        if self.selected_profile != CUSTOM_PROFILE_KEY:
            self.selected_profile = CUSTOM_PROFILE_KEY
            for card_key, card in self.profile_cards.items():
                card.set_selected(card_key == CUSTOM_PROFILE_KEY)

    # -- carga / guardado de config.yaml ---------------------------------------
    def _load_config_into_form(self) -> None:
        self.config = core.load_config()

        self.codec_combo.setCurrentText(self.config.get("video_codec") or "auto")
        self.encoder_edit.setText(self.config.get("video_encoder") or "")
        self.bitrate_edit.setText(self.config.get("bitrate_max") or "")
        self.max_size_spin.setValue(self.config.get("max_size") or 0)
        self.fullscreen_check.setChecked(bool(self.config.get("fullscreen", False)))
        self.wifi_port_spin.setValue(self.config.get("wifi_port") or wireless.DEFAULT_PORT)
        self.input_mode_combo.setCurrentText(self.config.get("input_mode") or "uhid")
        stored_route = self.config.get("audio_route")
        if stored_route not in ("off", "scrcpy", "bluetooth", "wifi_dual"):
            stored_route = "scrcpy" if self.config.get("audio", False) else "off"
        if stored_route == "wifi_dual":
            # No se re-provisiona la conexion de audio por WiFi solo por
            # cargar el archivo (requiere el celular detectado por USB
            # primero); se cae a "off" hasta que el usuario la re-elija.
            stored_route = "off"
        self._set_audio_route_combo(stored_route)
        self._last_audio_route = stored_route
        self.audio_codec_combo.setCurrentText(self.config.get("audio_codec") or "auto")
        self.audio_bitrate_edit.setText(self.config.get("audio_bitrate") or "")
        self.audio_buffer_spin.setValue(int(self.config.get("audio_buffer") or 50))
        self._update_audio_advanced_enabled(stored_route)
        self.fix_usb_check.setChecked(bool(self.config.get("auto_fix_usb_power", False)))
        self.fix_cpu_check.setChecked(bool(self.config.get("auto_fix_cpu_governor", False)))
        self.extra_flags_edit.setText(" ".join(self.config.get("extra_flags", [])))
        self.max_fps_spin.setValue(int(self.config.get("max_fps") or 0))
        self.fps_overlay_check.setChecked(bool(self.config.get("fps_overlay", True)))
        stored_position = self.config.get("fps_overlay_position") or "bottom-left"
        idx = self.fps_position_combo.findData(stored_position)
        self.fps_position_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._check_stream_thresholds()

        default_key = CUSTOM_PROFILE_KEY
        if default_key in self.profile_cards:
            self.profile_cards[default_key].set_selected(True)
        self.selected_profile = default_key

    def _form_to_overrides(self) -> dict:
        overrides: dict = {}
        codec = self.codec_combo.currentText()
        if codec != "auto":
            overrides["video_codec"] = codec
        if self.encoder_edit.text().strip():
            overrides["video_encoder"] = self.encoder_edit.text().strip()
        if self.bitrate_edit.text().strip():
            overrides["bitrate_max"] = self.bitrate_edit.text().strip()
        if self.max_size_spin.value() > 0:
            overrides["max_size"] = self.max_size_spin.value()
        overrides["fullscreen"] = self.fullscreen_check.isChecked()
        overrides["wifi_port"] = self.wifi_port_spin.value()
        overrides["input_mode"] = self.input_mode_combo.currentText()
        audio_route = self.audio_route_combo.currentData()
        overrides["audio_route"] = audio_route
        overrides["audio"] = audio_route == "scrcpy"  # retrocompatibilidad con config.yaml viejos
        if audio_route in ("scrcpy", "wifi_dual"):
            codec = self.audio_codec_combo.currentText()
            if codec != "auto":
                overrides["audio_codec"] = codec
            if self.audio_bitrate_edit.text().strip():
                overrides["audio_bitrate"] = self.audio_bitrate_edit.text().strip()
            overrides["audio_buffer"] = self.audio_buffer_spin.value()
        overrides["auto_fix_usb_power"] = self.fix_usb_check.isChecked()
        overrides["auto_fix_cpu_governor"] = self.fix_cpu_check.isChecked()
        flags = self.extra_flags_edit.text().split()
        overrides["extra_flags"] = flags
        overrides["max_fps"] = self.max_fps_spin.value()
        # fps_overlay activa --print-fps puertas adentro: si el usuario no
        # quiere ver el contador, no tiene sentido gastar ciclos
        # imprimiendolo a la consola.
        overrides["fps_overlay"] = self.fps_overlay_check.isChecked()
        overrides["fps_overlay_position"] = self.fps_position_combo.currentData()
        return overrides

    def on_save_config(self) -> None:
        merged = dict(self.config)
        merged.update(self._form_to_overrides())
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            yaml.safe_dump(merged, fh, allow_unicode=True, sort_keys=False)
        self.config = merged
        self._log("Configuracion guardada en config.yaml.")
        self.status_message.emit("Configuracion guardada")

    # -- deteccion --------------------------------------------------------------
    def on_detect(self) -> None:
        self.detect_btn.setEnabled(False)
        self._log("Detectando setup...")
        self.status_message.emit("Detectando celular, USB, monitor y CPU...")
        self.device_status.emit("Detectando...", False)
        self.detect_worker = DetectWorker(self.config)
        self.detect_worker.done.connect(self._on_detect_done)
        self.detect_worker.failed.connect(self._on_detect_failed)
        self.detect_worker.start()

    def _on_detect_done(self, result) -> None:
        self.device, self.usb, self.monitor, self.cpu_status = result
        self.detect_btn.setEnabled(True)
        if wireless.is_wireless_serial(self.device.serial):
            self.connection_mode = "wifi"
            self.wifi_btn.setText("Volver a USB")
        else:
            self.connection_mode = "usb"
            self.usb_serial = self.device.serial
            self.wifi_btn.setText("Conectar por WiFi")
        self.wifi_btn.setEnabled(True)
        self.launch_btn.setEnabled(True)
        self.connect_status_label.setText(f"✓ {self.device.brand} {self.device.model} conectado")
        self.connect_status_label.setStyleSheet(
            f"color: {COLORS['green']}; font-size: 15px; font-weight: 700;"
        )
        self._fill_diagnosis()
        self._log("Deteccion completa.")
        self.status_message.emit(f"Detectado: {self.device.brand} {self.device.model}")
        self.device_status.emit(f"{self.device.brand} {self.device.model}", True)
        self._update_launch_summary()
        for btn in (self.res_use_monitor_btn, self.res_apply_btn, self.res_reset_btn, self.res_refresh_btn):
            btn.setEnabled(True)
        self.on_resolution_refresh()

    def on_restart_adb(self) -> None:
        self.adb_restart_btn.setEnabled(False)
        self.detect_btn.setEnabled(False)
        self._log("Reiniciando servidor ADB (kill-server + start-server)...")
        self.status_message.emit("Reiniciando ADB...")
        self.adb_restart_worker = AdbRestartWorker()
        self.adb_restart_worker.done.connect(self._on_adb_restart_done)
        self.adb_restart_worker.failed.connect(self._on_adb_restart_failed)
        self.adb_restart_worker.start()

    def _on_adb_restart_done(self, message: str) -> None:
        self.adb_restart_btn.setEnabled(True)
        self._log(f"ADB reiniciado: {message}")
        self.status_message.emit("ADB reiniciado. Detectando de nuevo...")
        self._toast("ADB reiniciado ✓", kind="success")
        # El reinicio del demonio invalida cualquier deteccion previa (el
        # celular que estaba antes puede ya no ser el que esta conectado
        # ahora), asi que se relanza "Detectar" automaticamente en vez de
        # dejar datos viejos en pantalla.
        self.on_detect()

    def _on_adb_restart_failed(self, message: str) -> None:
        self.adb_restart_btn.setEnabled(True)
        self.detect_btn.setEnabled(True)
        self._log(f"Error reiniciando ADB: {message}")
        self.status_message.emit("Error al reiniciar ADB")
        self._toast(f"No se pudo reiniciar ADB: {message}", kind="error")

    def _on_detect_failed(self, message: str) -> None:
        self.detect_btn.setEnabled(True)
        # Una deteccion fallida invalida cualquier celular/topologia USB
        # detectados antes: si no se limpia esto, "Lanzar scrcpy" seguia
        # habilitado y usaba los datos VIEJOS (ej. el serial de un celular
        # que ya no esta conectado porque se cambio por otro), lanzando
        # scrcpy contra un dispositivo que no existe y sin avisar nada.
        self.device = None
        self.usb = None
        self.usb_serial = None
        self.launch_btn.setEnabled(False)
        self.wifi_btn.setEnabled(False)
        for btn in (self.res_use_monitor_btn, self.res_apply_btn, self.res_reset_btn, self.res_refresh_btn):
            btn.setEnabled(False)
        self.phone_resolution_status.setText("Detecta el celular para ver su resolución.")
        self.connect_status_label.setText("✗ No se pudo detectar el celular")
        self.connect_status_label.setStyleSheet(
            f"color: {COLORS['red']}; font-size: 15px; font-weight: 700;"
        )
        self._log(f"Error: {message}")
        self.status_message.emit("Error al detectar el setup")
        self.device_status.emit("Sin conectar", False)
        self._update_launch_summary()
        QMessageBox.warning(self, "Error de deteccion", message)

    # -- WiFi / LAN ---------------------------------------------------------
    def on_toggle_wifi(self) -> None:
        if self.connection_mode == "wifi":
            self.on_disable_wifi()
        else:
            self.on_enable_wifi()

    def on_enable_wifi(self) -> None:
        if not self.device:
            QMessageBox.information(
                self, "Falta detectar",
                "Primero pulsa 'Detectar' con el celular "
                "conectado por USB."
            )
            return
        if wireless.is_wireless_serial(self.device.serial):
            return
        self.usb_serial = self.device.serial
        self.wifi_btn.setEnabled(False)
        self._log("Activando modo WiFi/LAN (no desconectes el cable todavia)...")
        self.status_message.emit("Cambiando a WiFi... no desconectes el cable todavia")
        port = self.wifi_port_spin.value()
        self.wifi_worker = WifiEnableWorker(self.usb_serial, port)
        self.wifi_worker.done.connect(self._on_wifi_enabled)
        self.wifi_worker.failed.connect(self._on_wifi_failed)
        self.wifi_worker.start()

    def _on_wifi_enabled(self, address: str) -> None:
        self.device.serial = address
        self.connection_mode = "wifi"
        self.wifi_btn.setText("Volver a USB")
        self.wifi_btn.setEnabled(True)
        self._fill_diagnosis()
        self._log(f"Conectado por WiFi en {address}. Ya puedes desconectar el cable USB.")
        self.status_message.emit(f"Conectado por WiFi: {address}")
        self.device_status.emit(f"{self.device.brand} {self.device.model} (WiFi)", True)

    def on_disable_wifi(self) -> None:
        if not self.device:
            return
        address = self.device.serial
        self.wifi_btn.setEnabled(False)
        self._log("Cerrando la conexion WiFi...")
        self.wifi_worker = WifiDisableWorker(address)
        self.wifi_worker.done.connect(self._on_wifi_disabled)
        self.wifi_worker.failed.connect(self._on_wifi_failed)
        self.wifi_worker.start()

    def _on_wifi_disabled(self) -> None:
        self.connection_mode = "usb"
        self.wifi_btn.setText("Conectar por WiFi")
        self.wifi_btn.setEnabled(bool(self.usb_serial))
        self._log(
            "Conexion WiFi cerrada. Reconecta el cable USB y pulsa "
            "'Detectar' de nuevo."
        )
        self.status_message.emit("Reconecta el cable USB y detecta de nuevo")
        QMessageBox.information(
            self, "Volver a USB",
            "Se cerro la conexion WiFi. Reconecta el cable USB y pulsa "
            "\"Detectar\" de nuevo para refrescar el setup."
        )

    def _on_wifi_failed(self, message: str) -> None:
        self.wifi_btn.setEnabled(True)
        self._log(f"Error de WiFi: {message}")
        self.status_message.emit("Error en la conexion WiFi")
        QMessageBox.warning(self, "No se pudo completar la operacion WiFi", message)

    def _fill_diagnosis(self) -> None:
        encoder = self.device.best_encoder if self.device else None
        conexion = (
            f"WiFi ({self.device.serial})"
            if wireless.is_wireless_serial(self.device.serial)
            else "USB"
        )
        values = {
            "Conexión": conexion,
            "Celular": f"{self.device.brand} {self.device.model} (Android {self.device.android_version})",
            "Chipset": self.device.chipset,
            "Mejor encoder": (
                f"{encoder.name} ({encoder.codec}, {encoder.kind})" if encoder else "no detectado"
            ),
            "Velocidad USB": self.usb.speed_label,
            "USB autosuspend": "si" if self.usb.any_autosuspend else "no",
            "Monitor": f"{self.monitor.name} {self.monitor.width}x{self.monitor.height}",
            "CPU governor": self.cpu_status.governor,
        }
        for field_name, value in values.items():
            self.diag_cards[field_name].set_value(value)

    # -- comando / lanzamiento ----------------------------------------------------
    def _build_overrides(self) -> dict:
        overrides = dict(self.config)
        overrides.update(self._form_to_overrides())
        return overrides

    def _current_result(self):
        if not self.device:
            QMessageBox.information(
                self, "Falta detectar",
                "Primero pulsa 'Detectar' para leer tu setup."
            )
            return None
        overrides = self._build_overrides()
        return command_builder.build(
            self.device, self.usb, self.monitor, self.cpu_status, overrides=overrides
        )

    def _combined_command_text(self, result, overrides: dict) -> str:
        text = "$ " + result.as_string()
        if overrides.get("audio_route") == "wifi_dual" and self.audio_wifi_address:
            sidecar = command_builder.build_audio_sidecar(self.audio_wifi_address, overrides)
            text += "\n\n# Audio por WiFi (instancia aparte, en paralelo):\n$ " + sidecar.as_string()
        return text

    def on_build_command(self) -> None:
        if not self.device:
            QMessageBox.information(
                self, "Falta detectar",
                "Primero pulsa 'Detectar' para leer tu setup."
            )
            return
        overrides = self._build_overrides()
        result = command_builder.build(
            self.device, self.usb, self.monitor, self.cpu_status, overrides=overrides
        )
        for warning in result.warnings:
            self._log(f"Aviso: {warning}")
        self.command_view.setPlainText(self._combined_command_text(result, overrides))

    def _verify_device_still_connected(self) -> bool:
        """
        Antes de lanzar, confirma que el celular detectado sigue siendo
        el que esta conectado ahora mismo. Sin este chequeo, si cambiabas
        de celular sin volver a pulsar "Detectar" con exito, "Lanzar
        scrcpy" segui usando el serial VIEJO (el de un celular que ya no
        esta ahi) y el comando fallaba o se quedaba colgado sin avisar
        nada claro en la pantalla principal.
        """
        try:
            connected = android_device.list_connected_serials(target=self.device.serial)
        except android_device.AndroidDeviceError as exc:
            QMessageBox.warning(
                self, "El celular ya no esta disponible",
                f"{exc}\n\nSi cambiaste de celular, prueba \"Reiniciar ADB\" "
                f"y despues \"Detectar\" de nuevo."
            )
            return False
        if self.device.serial not in connected:
            QMessageBox.warning(
                self, "El celular ya no esta disponible",
                f"El celular detectado antes ({self.device.brand} "
                f"{self.device.model}, serial {self.device.serial}) ya no "
                f"aparece conectado. Si lo cambiaste por otro celular o "
                f"reconectaste el cable, pulsa \"Detectar\" de nuevo antes "
                f"de lanzar (o \"Reiniciar ADB\" si sigue sin aparecer)."
            )
            return False
        return True

    def on_launch(self) -> None:
        if not self.device:
            QMessageBox.information(
                self, "Falta detectar",
                "Primero pulsa 'Detectar' para leer tu setup."
            )
            return
        if not self._verify_device_still_connected():
            return
        overrides = self._build_overrides()
        result = command_builder.build(
            self.device, self.usb, self.monitor, self.cpu_status, overrides=overrides
        )
        for warning in result.warnings:
            self._log(f"Aviso: {warning}")

        sidecar_command = None
        if overrides.get("audio_route") == "wifi_dual":
            if not self.audio_wifi_address:
                QMessageBox.warning(
                    self, "Audio por WiFi (dual)",
                    "Elegiste audio por WiFi (dual) pero el canal todavia "
                    "no esta listo. Volve a elegir esa opcion en el combo "
                    "de Audio y espera a que confirme antes de lanzar."
                )
                return
            sidecar = command_builder.build_audio_sidecar(self.audio_wifi_address, overrides)
            for warning in sidecar.warnings:
                self._log(f"Aviso (audio WiFi): {warning}")
            sidecar_command = sidecar.as_command()

        self.command_view.setPlainText(self._combined_command_text(result, overrides))

        if self.fix_usb_check.isChecked() or self.fix_cpu_check.isChecked():
            self.status_message.emit("Esperando autenticacion grafica...")

        self.launch_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.launch_worker = LaunchWorker(
            result.as_command(),
            self.usb,
            self.fix_usb_check.isChecked(),
            self.fix_cpu_check.isChecked(),
        )
        self.launch_worker.log.connect(self._log)
        self.launch_worker.fps_update.connect(self._on_fps_update)
        self.launch_worker.finished_ok.connect(self._on_launch_done)
        self.launch_worker.failed.connect(self._on_launch_failed)
        self.launch_worker.start()

        if sidecar_command is not None:
            self._log("Lanzando audio por WiFi en paralelo...")
            self.audio_launch_worker = LaunchWorker(sidecar_command, self.usb, False, False)
            self.audio_launch_worker.log.connect(lambda line: self._log(f"[audio] {line}"))
            self.audio_launch_worker.finished_ok.connect(
                lambda: self._log("Instancia de audio por WiFi cerrada.")
            )
            self.audio_launch_worker.failed.connect(
                lambda msg: self._log(f"Audio por WiFi termino con error: {msg}")
            )
            self.audio_launch_worker.start()

        if self.fps_overlay_check.isChecked():
            if self.fps_overlay_window is None:
                self.fps_overlay_window = FpsOverlay(log=self._log)
            self.fps_overlay_window.set_fps(0)
            self.fps_overlay_window.show(self.fps_position_combo.currentData())

    def on_stop(self) -> None:
        if not self.launch_worker:
            return
        self.stop_btn.setEnabled(False)
        self._log("Deteniendo transmision...")
        self.status_message.emit("Deteniendo transmision...")
        self.launch_worker.stop()
        if self.audio_launch_worker:
            self.audio_launch_worker.stop()
        # Si scrcpy no cierra solo en unos segundos (colgado, etc.), se
        # fuerza el cierre para que el boton nunca quede "trabado".
        QTimer.singleShot(4000, self._force_kill_if_needed)

    def _force_kill_if_needed(self) -> None:
        worker = self.launch_worker
        if worker and worker.process and worker.process.poll() is None:
            self._log("scrcpy no respondio a tiempo; forzando cierre.")
            worker.kill()
        audio_worker = self.audio_launch_worker
        if audio_worker and audio_worker.process and audio_worker.process.poll() is None:
            audio_worker.kill()

    def _on_fps_update(self, value: float) -> None:
        if self.fps_overlay_window is not None:
            self.fps_overlay_window.set_fps(value)

    def _on_launch_done(self) -> None:
        self.launch_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if self.fps_overlay_window is not None:
            self.fps_overlay_window.hide()
        self.status_message.emit("scrcpy cerrado")

    def _on_launch_failed(self, message: str) -> None:
        self.launch_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if self.fps_overlay_window is not None:
            self.fps_overlay_window.hide()
        self._log(f"Error al lanzar: {message}")
        self.status_message.emit("Error al lanzar scrcpy")
        QMessageBox.warning(self, "Error al lanzar scrcpy", message)

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _toast(self, message: str, kind: str = "info") -> None:
        """Notificacion chica y no bloqueante (ver clase Toast). kind:
        'success' | 'error' | 'info'. Complementa al log de la pestana
        "Registro" -- esto es lo que se ve al toque, sin tener que ir a
        buscarlo."""
        self.toast_requested.emit(message, kind)

    def cleanup(self) -> None:
        """Se llama al cerrar NexoHub -- termina el proceso auxiliar del
        overlay de FPS si quedo corriendo, para no dejarlo huerfano."""
        if self.fps_overlay_window is not None:
            self.fps_overlay_window.close()


# --------------------------------------------------------------------------
# Notificaciones no bloqueantes ("toast")
# --------------------------------------------------------------------------
class Toast(QFrame):
    """Notificacion chica que aparece sobre el contenido de la ventana y
    se cierra sola -- para avisos rapidos de exito/error/info que no
    necesitan que el usuario decida nada (a diferencia de un
    QMessageBox, que bloquea hasta que le das OK)."""

    closed = pyqtSignal(object)

    _ACCENT_KEY = {"success": "green", "error": "red", "info": "accent"}
    _ICON = {"success": "✓", "error": "✕", "info": "ℹ"}

    def __init__(self, parent: QWidget, message: str, kind: str = "info"):
        super().__init__(parent)
        color = COLORS.get(self._ACCENT_KEY.get(kind, "accent"), COLORS["accent"])
        self.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['surface0']}; "
            f"border: 1px solid {COLORS['surface2']}; "
            f"border-left: 4px solid {color}; border-radius: 10px; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 8, 10)
        layout.setSpacing(10)

        icon = QLabel(self._ICON.get(kind, "ℹ"))
        icon.setStyleSheet(
            f"color: {color}; font-size: 15px; font-weight: 800; "
            f"border: none; background: transparent;"
        )
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        label = QLabel(message)
        label.setWordWrap(True)
        label.setMaximumWidth(300)
        label.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 12px; font-weight: 600; "
            f"border: none; background: transparent;"
        )
        layout.addWidget(label, 1)

        close_btn = QToolButton()
        close_btn.setText("×")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {COLORS['subtext']}; "
            f"border: none; font-size: 16px; font-weight: 700; }}"
            f"QToolButton:hover {{ color: {COLORS['text']}; }}"
        )
        close_btn.clicked.connect(self.dismiss)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.setFixedWidth(340)
        self.adjustSize()

        # Los errores se quedan mas tiempo (hay algo que leer y quizas
        # actuar), el resto es solo una confirmacion de "listo".
        duration_ms = 6000 if kind == "error" else 2800
        QTimer.singleShot(duration_ms, self.dismiss)

    def dismiss(self) -> None:
        self.closed.emit(self)
        self.hide()
        self.deleteLater()


# --------------------------------------------------------------------------
# Ventana principal
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_ICON} {APP_NAME}")
        self.resize(980, 920)
        self._toasts: list[Toast] = []

        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_topbar())

        self.config_widget = ConfigWidget()
        self.config_widget.status_message.connect(self._show_status)
        self.config_widget.device_status.connect(self._on_device_status)
        self.config_widget.toast_requested.connect(self._show_toast)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("contentArea")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(28, 24, 28, 18)
        inner_layout.addWidget(self.config_widget)
        scroll.setWidget(inner)
        root_layout.addWidget(scroll, 1)

        # Barra de acciones fija, FUERA del scroll: siempre visible sin
        # importar cuanto hayas bajado o que acordeon tengas abierto.
        bar_wrap = QWidget()
        bar_wrap_layout = QVBoxLayout(bar_wrap)
        bar_wrap_layout.setContentsMargins(28, 0, 28, 18)
        bar_wrap_layout.addWidget(self.config_widget.action_bar)
        root_layout.addWidget(bar_wrap)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self._show_status("Listo")

    def _build_topbar(self) -> QWidget:
        topbar = QWidget()
        topbar.setObjectName("topbar")
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(28, 16, 28, 16)
        layout.setSpacing(14)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        glow = QLabel(topbar)
        glow.setFixedSize(46, 46)
        glow.move(-4, -4)
        glow.setStyleSheet(f"background-color: rgba(224, 48, 63, 90); border-radius: 23px;")
        glow_effect = QGraphicsBlurEffect()
        glow_effect.setBlurRadius(22)
        glow.setGraphicsEffect(glow_effect)
        glow.lower()
        icon = QLabel(APP_ICON)
        icon.setFixedSize(34, 34)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"font-size: 17px; background-color: {COLORS['accent_soft']}; "
            f"border: 1px solid {COLORS['accent_border']}; border-radius: 9px;"
        )
        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        name = QLabel(APP_NAME)
        name.setObjectName("brand")
        subtitle = QLabel("control de streaming")
        subtitle.setObjectName("brandSubtitle")
        brand_box.addWidget(name)
        brand_box.addWidget(subtitle)
        brand_row.addWidget(icon)
        brand_row.addLayout(brand_box)
        layout.addLayout(brand_row)

        layout.addStretch(1)

        for badge_text in ("Linux", "Android", "Scrcpy"):
            badge = QLabel(badge_text)
            badge.setProperty("role", "pill")
            layout.addWidget(badge)

        status_chip = QFrame()
        status_chip.setObjectName("statusChip")
        status_chip_layout = QHBoxLayout(status_chip)
        status_chip_layout.setContentsMargins(14, 8, 16, 8)
        status_chip_layout.setSpacing(8)
        self.status_dot = QLabel()
        self.status_dot.setObjectName("statusDot")
        self.status_dot.setStyleSheet(f"background-color: {COLORS['surface2']};")
        self.status_text = QLabel("Sin conectar")
        self.status_text.setObjectName("statusText")
        status_chip_layout.addWidget(self.status_dot)
        status_chip_layout.addWidget(self.status_text)
        layout.addWidget(status_chip)

        exit_btn = QPushButton("⏻")
        exit_btn.setObjectName("exitButton")
        exit_btn.setFixedWidth(38)
        exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exit_btn.setToolTip("Cierra NexoHub (no cierra scrcpy si ya esta transmitiendo).")
        exit_btn.clicked.connect(self._on_exit_requested)
        layout.addWidget(exit_btn)

        return topbar

    def _on_device_status(self, text: str, ok: bool) -> None:
        color = COLORS["green"] if ok else COLORS["surface2"]
        self.status_dot.setStyleSheet(f"background-color: {color};")
        self.status_text.setText(text)

    # -- notificaciones tipo "toast" -------------------------------------------
    def _show_toast(self, message: str, kind: str) -> None:
        toast = Toast(self, message, kind)
        toast.closed.connect(self._on_toast_closed)
        self._toasts.append(toast)
        toast.show()
        toast.raise_()
        self._reposition_toasts()

    def _on_toast_closed(self, toast: "Toast") -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._reposition_toasts()

    def _reposition_toasts(self) -> None:
        margin = 22
        spacing = 10
        y = self.height() - margin
        for toast in reversed(self._toasts):
            y -= toast.height()
            toast.move(self.width() - toast.width() - margin, y)
            y -= spacing

    def resizeEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        super().resizeEvent(event)
        self._reposition_toasts()

    def _on_exit_requested(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Salir de NexoHub",
            "¿Seguro que quieres cerrar NexoHub?",
        )
        if confirm == QMessageBox.StandardButton.Yes:
            QApplication.instance().quit()

    def closeEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        # Sea por el boton "Salir" o por la X de la ventana: hay que
        # terminar el proceso auxiliar del overlay de FPS si quedo
        # corriendo, para no dejarlo huerfano en segundo plano. No toca
        # scrcpy en si -- ese sigue vivo a proposito si ya esta
        # transmitiendo.
        self.config_widget.cleanup()
        super().closeEvent(event)

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)


def main() -> None:
    app = QApplication(sys.argv)
    # Fuente explicita en vez de dejar el default del sistema: se ve mas
    # consistente entre distros/temas de Hyprland. Si "Inter" no esta
    # instalada, Qt cae automaticamente a la mejor sans-serif disponible.
    font = QFont("Inter")
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
