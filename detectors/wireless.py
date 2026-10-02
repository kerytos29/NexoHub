"""
Maneja el cambio de ADB de USB a WiFi/LAN (modo `adb tcpip`) para poder
usar scrcpy sin cable una vez que el celular ya fue detectado por USB.

Flujo tipico:
    1. El celular esta conectado por USB y ya fue detectado (tiene un
       serial "de fabrica", no una direccion IP).
    2. `enable_over_wifi(serial)` le pide la IP de WiFi al celular via
       ADB, activa el demonio ADB en modo TCP/IP en ese mismo puerto, y
       hace `adb connect ip:puerto`.
    3. A partir de ahi se puede desconectar el cable: el "serial" para
       todo lo demas (scrcpy, getprop, etc.) pasa a ser "ip:puerto".

Requisito de red: el celular y el PC deben estar en la misma red LAN
(mismo router/WiFi). No sirve para conectarse a traves de internet.
"""
from __future__ import annotations

import re
import subprocess
import time

DEFAULT_PORT = 5555

# Un serial "inalambrico" tiene forma ip:puerto (ej. 192.168.1.23:5555).
# Un serial USB de fabrica nunca tiene ese formato (son alfanumericos
# tipo "R58M12ABCD" o similares), asi que este patron alcanza para
# distinguir uno de otro sin tener que recordar el estado por separado.
_WIRELESS_SERIAL_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}$")


class WirelessError(RuntimeError):
    pass


def is_wireless_serial(serial: str) -> bool:
    """True si el serial es una direccion ip:puerto (conexion WiFi/LAN)."""
    return bool(_WIRELESS_SERIAL_RE.match(serial or ""))


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise WirelessError(f"No se pudo ejecutar: {' '.join(cmd)}") from exc
    except subprocess.TimeoutExpired as exc:
        raise WirelessError(f"Se agoto el tiempo de espera: {' '.join(cmd)}") from exc


def get_device_wifi_ip(serial: str) -> str:
    """
    Pregunta al celular (por USB, via ADB) cual es su IP en la interfaz
    WiFi. Prueba primero `ip route` (mas confiable, no depende del
    nombre de la interfaz) y cae a `ip addr show wlan0` si hace falta.
    """
    # "ip route get 1" devuelve la ruta que tomaria un paquete saliente,
    # con el patron "... src <IP> ...": funciona sin importar si la
    # interfaz WiFi se llama wlan0, wlan1, etc.
    result = _run(["adb", "-s", serial, "shell", "ip", "route", "get", "1"])
    match = re.search(r"\bsrc\s+(\d{1,3}(?:\.\d{1,3}){3})\b", result.stdout)
    if match:
        return match.group(1)

    # Fallback: interfaz wlan0 explicita.
    result = _run(["adb", "-s", serial, "shell", "ip", "-f", "inet", "addr", "show", "wlan0"])
    match = re.search(r"inet\s+(\d{1,3}(?:\.\d{1,3}){3})/", result.stdout)
    if match:
        return match.group(1)

    raise WirelessError(
        "No se pudo obtener la IP de WiFi del celular. Asegurate de que "
        "el WiFi este activado y conectado a una red (no solo con datos "
        "moviles)."
    )


def enable_tcpip(serial: str, port: int = DEFAULT_PORT) -> None:
    """Activa el demonio ADB en modo TCP/IP en el puerto indicado."""
    result = _run(["adb", "-s", serial, "tcpip", str(port)])
    if result.returncode != 0:
        raise WirelessError(
            f"No se pudo activar ADB por TCP/IP: {result.stderr.strip() or result.stdout.strip()}"
        )


def connect(address: str, timeout: int = 15) -> None:
    """Corre `adb connect ip:puerto` y valida la respuesta."""
    result = _run(["adb", "connect", address], timeout=timeout)
    output = (result.stdout + result.stderr).lower()
    if "connected to" in output or "already connected" in output:
        return
    raise WirelessError(
        f"'adb connect {address}' no confirmo la conexion "
        f"({result.stdout.strip() or result.stderr.strip() or 'sin respuesta'}). "
        f"Verifica que el celular y el PC esten en la misma red WiFi."
    )


def disconnect(address: str) -> None:
    """Corre `adb disconnect ip:puerto`. No falla si ya estaba desconectado."""
    _run(["adb", "disconnect", address])


def enable_over_wifi(usb_serial: str, port: int = DEFAULT_PORT) -> str:
    """
    Orquesta el cambio completo de USB a WiFi para un celular que ya
    esta conectado por USB. Devuelve el nuevo "serial" (ip:puerto) que
    hay que usar en adelante para todo (scrcpy, getprop, etc).

    Requiere que el cable USB siga conectado durante este paso (recien
    despues de conectar por WiFi se puede desenchufar).
    """
    if is_wireless_serial(usb_serial):
        raise WirelessError(
            "Este celular ya esta conectado por WiFi, no por USB. "
            "Reconectalo por cable para poder reactivar el modo TCP/IP."
        )

    ip = get_device_wifi_ip(usb_serial)
    enable_tcpip(usb_serial, port)
    # El demonio ADB tarda un instante en reiniciar en modo TCP/IP antes
    # de aceptar conexiones entrantes.
    time.sleep(1.5)
    address = f"{ip}:{port}"
    connect(address)
    return address
