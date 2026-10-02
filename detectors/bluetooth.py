"""
Estado (solo lectura) del Bluetooth del PC, para la ruta de audio
"Bluetooth externo": el celular se empareja directo con el PC desde
Android (Ajustes > Bluetooth > elegir el PC como salida de audio) y el
audio del juego sale por ahi, sin pasar nunca por scrcpy -- por eso no
compite con el video/control por CPU ni por el cable/WiFi.

Este modulo NO empareja ni conecta nada (eso requiere el agente de
emparejamiento interactivo de bluetoothctl, con confirmacion de PIN, y
se hace una vez a mano); solo informa si hay un adaptador disponible y
que dispositivos ya estan emparejados/conectados, para mostrarlo en la
GUI como ayuda de diagnostico.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field


class BluetoothError(RuntimeError):
    pass


@dataclass
class BluetoothStatus:
    available: bool  # hay un adaptador Bluetooth utilizable
    powered: bool  # el adaptador esta encendido
    paired_devices: list[str] = field(default_factory=list)
    connected_devices: list[str] = field(default_factory=list)
    # True si no se pudo distinguir "emparejado" de "conectado ahora"
    # (bluetoothctl viejo, sin el filtro "devices Connected"): en ese
    # caso paired_devices es la unica lista confiable.
    connected_unknown: bool = False


def _bluetoothctl(*args: str, timeout: int = 6) -> str:
    try:
        result = subprocess.run(
            ["bluetoothctl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BluetoothError(
            "bluetoothctl no esta instalado (paquete 'bluez-utils' en Arch)."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BluetoothError("bluetoothctl no respondio a tiempo.") from exc
    return result.stdout


def _parse_device_lines(output: str) -> list[str]:
    devices = []
    for line in output.splitlines():
        parts = line.strip().split(" ", 2)
        if len(parts) == 3 and parts[0] == "Device":
            devices.append(parts[2])
    return devices


def detect() -> BluetoothStatus:
    """
    Chequea si hay un adaptador Bluetooth y que dispositivos de audio
    tiene emparejados/conectados. No lanza excepcion si simplemente no
    hay Bluetooth en el PC (available=False); si lanza BluetoothError es
    porque falta el binario o algo se colgo.
    """
    if shutil.which("bluetoothctl") is None:
        return BluetoothStatus(available=False, powered=False)

    show_output = _bluetoothctl("show")
    if not show_output.strip() or "No default controller available" in show_output:
        return BluetoothStatus(available=False, powered=False)

    powered = "Powered: yes" in show_output
    paired = _parse_device_lines(_bluetoothctl("devices"))

    # El filtro "devices Connected" requiere bluez relativamente moderno
    # (>=5.65). En versiones viejas el comando falla o ignora el filtro;
    # en ese caso no se garantiza que la lista distinga conectados de
    # solo-emparejados, asi que se marca connected_unknown.
    try:
        connected_output = _bluetoothctl("devices", "Connected")
        connected = _parse_device_lines(connected_output)
        connected_unknown = False
    except BluetoothError:
        connected = []
        connected_unknown = True

    return BluetoothStatus(
        available=True,
        powered=powered,
        paired_devices=paired,
        connected_devices=connected,
        connected_unknown=connected_unknown,
    )
