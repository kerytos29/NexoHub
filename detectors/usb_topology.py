"""
Detecta la topologia USB del celular conectado: velocidad realmente
negociada (no la del cable) y el estado de power/control de toda la
cadena de hubs hasta el dispositivo.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

try:
    import pyudev
except ImportError:  # pragma: no cover
    pyudev = None


SPEED_LABELS = {
    "1.5": "USB 1.0 (low-speed)",
    "12": "USB 1.1 (full-speed)",
    "480": "USB 2.0 (high-speed)",
    "5000": "USB 3.0 (super-speed)",
    "10000": "USB 3.1 Gen2",
}


@dataclass
class UsbNode:
    sys_path: str
    speed_mbps: str = "unknown"
    power_control: str = "unknown"  # "auto" u "on"


@dataclass
class UsbTopology:
    device_node: UsbNode | None = None
    parent_hubs: list[UsbNode] = field(default_factory=list)

    @property
    def negotiated_speed(self) -> str:
        if self.device_node is None:
            return "unknown"
        return self.device_node.speed_mbps

    @property
    def speed_label(self) -> str:
        return SPEED_LABELS.get(self.negotiated_speed, self.negotiated_speed)

    @property
    def any_autosuspend(self) -> bool:
        nodes = ([self.device_node] if self.device_node else []) + self.parent_hubs
        return any(n.power_control == "auto" for n in nodes)


class UsbTopologyError(RuntimeError):
    pass


def _read_attr(device, name: str, default: str = "unknown") -> str:
    value = device.attributes.get(name)
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode(errors="replace").strip()
    return str(value).strip()


def find_topology_by_serial(android_serial: str) -> UsbTopology:
    """
    Busca en el arbol udev el dispositivo USB cuyo serial coincide con el
    serial ADB del celular, y sube por la cadena de hubs padres.
    """
    if pyudev is None:
        raise UsbTopologyError(
            "Falta la libreria pyudev. Instalala con: pip install pyudev"
        )

    context = pyudev.Context()
    target = None
    for device in context.list_devices(subsystem="usb", DEVTYPE="usb_device"):
        serial = _read_attr(device, "serial", "")
        if serial and serial == android_serial:
            target = device
            break

    if target is None:
        raise UsbTopologyError(
            f"No se encontro un dispositivo USB con serial '{android_serial}'. "
            f"¿El celular sigue conectado?"
        )

    topology = UsbTopology()
    topology.device_node = UsbNode(
        sys_path=target.sys_path,
        speed_mbps=_read_attr(target, "speed"),
        power_control=_read_attr(target, "power/control"),
    )

    parent = target.parent
    while parent is not None:
        if parent.subsystem == "usb" and parent.device_type == "usb_device":
            topology.parent_hubs.append(
                UsbNode(
                    sys_path=parent.sys_path,
                    speed_mbps=_read_attr(parent, "speed"),
                    power_control=_read_attr(parent, "power/control"),
                )
            )
        parent = parent.parent

    return topology


def force_power_on(topology: UsbTopology) -> list[str]:
    """
    Fuerza power/control=on en el dispositivo y toda la cadena de hubs
    padres que esten en 'auto'. Requiere permisos de escritura en sysfs
    (root, o reglas udev instaladas). La elevacion se pide con pkexec
    (dialogo grafico de polkit) en vez de sudo en terminal. Devuelve la
    lista de rutas tocadas.
    """
    if shutil.which("pkexec") is None:
        raise UsbTopologyError(
            "No se encontro 'pkexec' en el PATH. Instalalo (paquete: polkit) "
            "para poder pedir la contrasena en una ventana grafica."
        )
    nodes = ([topology.device_node] if topology.device_node else []) + topology.parent_hubs
    touched = []
    for node in nodes:
        if node.power_control != "auto":
            continue
        path = f"{node.sys_path}/power/control"
        try:
            subprocess.run(
                ["pkexec", "tee", path],
                input="on\n",
                text=True,
                capture_output=True,
                check=True,
            )
            node.power_control = "on"
            touched.append(path)
        except subprocess.CalledProcessError as exc:
            if exc.returncode == 126:
                raise UsbTopologyError(
                    "Se cancelo la autenticacion (o no hay un agente de "
                    "polkit corriendo -- en Hyprland instala y arranca "
                    "'hyprpolkitagent' o 'polkit-gnome')."
                ) from exc
            raise UsbTopologyError(f"No se pudo escribir en {path}: {exc}") from exc
    return touched
