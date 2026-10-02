"""
Junta TODAS las acciones que necesitan privilegios de root al lanzar
scrcpy -- forzar power/control=on en cada hub de la cadena USB que
este en 'auto', y cambiar el CPU governor -- en una UNICA invocacion de
pkexec.

Antes cada cosa pedia su propio pkexec por separado: un dialogo de
contrasena por cada nodo USB en 'auto' (el celular + cada hub padre,
tipicamente 2-3) mas otro mas si tambien se activaba "Forzar CPU
performance". Con un celular detras de varios hubs, eso eran hasta 4
ventanas de contrasena seguidas para lanzar una sola vez. Ahora se arma
un unico script de shell con todo lo que haya que hacer y se le pide a
polkit UNA sola autenticacion para correrlo entero.
"""
from __future__ import annotations

import shutil
import subprocess

from . import usb_topology as usb_topology_mod


class PrivilegedActionError(RuntimeError):
    pass


def _usb_paths_needing_power_on(usb: "usb_topology_mod.UsbTopology | None") -> list[str]:
    if usb is None:
        return []
    nodes = ([usb.device_node] if usb.device_node else []) + usb.parent_hubs
    return [f"{node.sys_path}/power/control" for node in nodes if node.power_control == "auto"]


def apply_privileged_fixes(
    usb: "usb_topology_mod.UsbTopology | None",
    fix_usb: bool,
    fix_cpu: bool,
) -> list[str]:
    """
    Aplica los ajustes marcados (USB power / CPU governor) con una sola
    autenticacion grafica. Si no hay nada realmente pendiente (ej. el
    USB ya esta en 'on' y no se pidio tocar el CPU), no pide contrasena
    ni corre pkexec. Devuelve las rutas de USB que se tocaron, para
    loguearlas.
    """
    usb_paths = _usb_paths_needing_power_on(usb) if fix_usb else []

    if not usb_paths and not fix_cpu:
        return []

    if shutil.which("pkexec") is None:
        raise PrivilegedActionError(
            "No se encontro 'pkexec' en el PATH. Instalalo (paquete: polkit) "
            "para poder pedir la contrasena en una ventana grafica."
        )

    script_parts = []
    if usb_paths:
        quoted_paths = " ".join(f"'{p}'" for p in usb_paths)
        # tee escribe el mismo stdin ("on") a todas las rutas listadas a
        # la vez -- es lo que permite encender varios hubs con una sola
        # elevacion de privilegios en vez de una por cada uno.
        script_parts.append(f"echo on | tee {quoted_paths} >/dev/null")
    if fix_cpu:
        if shutil.which("cpupower") is None:
            raise PrivilegedActionError(
                "No se encontro 'cpupower' en el PATH. Instalalo (paquete: cpupower)."
            )
        script_parts.append("cpupower frequency-set -g performance")

    script = " && ".join(script_parts)
    try:
        subprocess.run(
            ["pkexec", "sh", "-c", script],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 126:
            raise PrivilegedActionError(
                "Se cancelo la autenticacion (o no hay un agente de polkit "
                "corriendo -- en Hyprland instala y arranca 'hyprpolkitagent' "
                "o 'polkit-gnome')."
            ) from exc
        raise PrivilegedActionError(
            f"No se pudieron aplicar los ajustes con privilegios: {exc.stderr.strip()}"
        ) from exc

    for node in ([usb.device_node] if usb and usb.device_node else []) + (usb.parent_hubs if usb else []):
        if f"{node.sys_path}/power/control" in usb_paths:
            node.power_control = "on"

    return usb_paths
