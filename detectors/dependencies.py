"""
Verifica que todo lo necesario para correr scrcpy-autoconfig este instalado,
y puede instalar automaticamente lo que falte.

Cubre dos capas:
  1. Binarios del sistema (adb, scrcpy, cpupower, pkexec) -- se instalan con
     el gestor de paquetes de la distro detectada, elevando permisos con
     pkexec (ventana grafica), nunca con sudo en terminal.
  2. Paquetes de Python (los de requirements.txt) -- se instalan con pip.

Esto es lo que hace posible que "instalar lo necesario" sea un boton en la
GUI en vez de una lista de comandos que el usuario tiene que copiar a mano.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REQUIREMENTS_PATH = Path(__file__).parent.parent / "requirements.txt"

# binario -> nombre del paquete por gestor. "cpupower" y "pkexec" son
# opcionales (habilitan --fix-cpu-governor y la contrasena grafica); "adb"
# y "scrcpy" son obligatorios para que el programa funcione en absoluto.
PACKAGE_MAP: dict[str, dict[str, str]] = {
    "adb": {
        "pacman": "android-tools",
        "apt": "android-tools-adb",
        "dnf": "android-tools",
        "zypper": "android-tools",
    },
    "scrcpy": {
        "pacman": "scrcpy",
        "apt": "scrcpy",
        "dnf": "scrcpy",
        "zypper": "scrcpy",
    },
    "cpupower": {
        "pacman": "cpupower",
        "apt": "linux-cpupower",
        "dnf": "kernel-tools",
        "zypper": "cpupower",
    },
    "pkexec": {
        "pacman": "polkit",
        "apt": "policykit-1",
        "dnf": "polkit",
        "zypper": "polkit",
    },
    # No es un binario suelto sino un conjunto de bindings de Python +
    # libreria del sistema; se detecta distinto (ver
    # _gtk_layer_shell_available) pero se instala igual que el resto,
    # por eso vive en el mismo mapa. Habilita el overlay de FPS estilo
    # BlueStacks (fps_overlay_helper.py) via wlr-layer-shell.
    "gtk-layer-shell": {
        "pacman": "python-gobject gtk3 gtk-layer-shell",
        "apt": "python3-gi gir1.2-gtk-3.0 gir1.2-gtklayershell-0.1",
        "dnf": "python3-gobject gtk3 gtk-layer-shell",
        "zypper": "python3-gobject gtk3 gtk-layer-shell",
    },
    # Habilita la pestaña de diagnostico de Bluetooth (ruta de audio
    # "Bluetooth externo"). Es de solo lectura -- nunca empareja nada.
    "bluetoothctl": {
        "pacman": "bluez-utils",
        "apt": "bluez",
        "dnf": "bluez",
        "zypper": "bluez",
    },
}

REQUIRED_BINARIES = ["adb", "scrcpy"]
OPTIONAL_BINARIES = ["cpupower", "pkexec", "gtk-layer-shell", "bluetoothctl"]

# Detalle mostrado junto a cada binario opcional en la pagina de
# Dependencias -- por defecto todos comparten un texto generico, pero
# gtk-layer-shell tiene uno propio para que quede claro para que sirve.
OPTIONAL_BINARY_DETAILS: dict[str, str] = {
    "gtk-layer-shell": "opcional (overlay de FPS estilo BlueStacks en Wayland)",
    "bluetoothctl": "opcional (diagnostico de Bluetooth para audio externo)",
}
DEFAULT_OPTIONAL_DETAIL = "opcional (fix USB/CPU y ventana de contraseña)"

# modulo importable -> nombre del paquete pip (cuando difieren)
PYTHON_PACKAGES = {
    "pyudev": "pyudev",
    "psutil": "psutil",
    "screeninfo": "screeninfo",
    "yaml": "pyyaml",
    "rich": "rich",
    "PyQt6": "PyQt6",
}

PACKAGE_MANAGERS = {
    "pacman": ["pacman", "-S", "--noconfirm", "--needed"],
    "apt": ["apt-get", "install", "-y"],
    "dnf": ["dnf", "install", "-y"],
    "zypper": ["zypper", "install", "-y"],
}

# Se corre ANTES de instalar, en la misma autenticacion (mismo pkexec):
# refresca la base de datos de paquetes de la distro. Sin esto, si la
# base local quedo desactualizada (algo muy comun si hace tiempo no se
# actualiza el sistema), pacman/apt intentan descargar una version de
# un paquete que los espejos ya borraron porque salio una mas nueva --
# eso da errores 404 en TODOS los espejos, no es un problema de red ni
# de NexoHub. Sincronizar primero evita ese caso en la gran mayoria de
# los casos (dnf/zypper ya refrescan solos si detectan la cache vieja,
# pero no cuesta nada pedirlo explicito).
PACKAGE_SYNC_COMMANDS = {
    "pacman": ["pacman", "-Sy", "--noconfirm"],
    "apt": ["apt-get", "update", "-qq"],
    "dnf": ["dnf", "makecache", "-y"],
    "zypper": ["zypper", "--non-interactive", "refresh"],
}

_MIRROR_ERROR_HINTS = ("404", "failed to retrieve", "no se pudo obtener", "not found on mirror")


def _gtk_layer_shell_available() -> bool:
    """
    gtk-layer-shell no es un binario en el PATH sino bindings de Python
    (PyGObject) + una libreria del sistema + su typelib GIR -- por eso
    se detecta importando, no con shutil.which. Si cualquiera de las
    tres piezas falta, el overlay de FPS estilo BlueStacks no puede
    arrancar y NexoHub se cae de vuelta a un overlay mas simple.
    """
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import Gtk, GtkLayerShell  # noqa: F401
    except Exception:  # noqa: BLE001 (cualquier fallo de import/typelib cuenta)
        return False
    return True


def gtk_layer_shell_available() -> bool:
    """Punto de entrada publico para que gui.py decida si vale la pena
    intentar levantar fps_overlay_helper.py."""
    return _gtk_layer_shell_available()


# binarios "especiales" cuya presencia no se puede chequear con
# shutil.which -- cada uno tiene su propia funcion de deteccion.
CUSTOM_BINARY_CHECKS = {
    "gtk-layer-shell": _gtk_layer_shell_available,
}


@dataclass
class DependencyReport:
    missing_binaries: list[str] = field(default_factory=list)
    missing_python: list[str] = field(default_factory=list)
    package_manager: str | None = None
    polkit_agent_running: bool = True

    @property
    def all_ok(self) -> bool:
        return not self.missing_binaries and not self.missing_python

    @property
    def install_commands(self) -> list[str]:
        """Comandos que se ejecutarian, solo para mostrar transparencia al usuario."""
        cmds = []
        if self.missing_binaries and self.package_manager:
            pkgs = self._map_binaries_to_packages()
            base = PACKAGE_MANAGERS[self.package_manager]
            cmds.append("pkexec " + " ".join(base + pkgs))
        if self.missing_python:
            cmds.append(
                f"{sys.executable} -m pip install --break-system-packages "
                + " ".join(self.missing_python)
            )
        return cmds

    def _map_binaries_to_packages(self) -> list[str]:
        pkgs = []
        for binary in self.missing_binaries:
            mapping = PACKAGE_MAP.get(binary, {})
            mapped = mapping.get(self.package_manager, binary)
            # algunas entradas (ej. gtk-layer-shell) son varios paquetes
            # separados por espacio en vez de uno solo.
            for pkg in mapped.split():
                if pkg not in pkgs:
                    pkgs.append(pkg)
        return pkgs


class DependencyError(RuntimeError):
    pass


def detect_package_manager() -> str | None:
    for manager in PACKAGE_MANAGERS:
        if shutil.which(manager.replace("get", "")) or shutil.which(manager):
            return manager
    # pacman/dnf/zypper se llaman igual que el binario; apt usa apt-get
    for manager, binary in (("pacman", "pacman"), ("dnf", "dnf"), ("zypper", "zypper"), ("apt", "apt-get")):
        if shutil.which(binary):
            return manager
    return None


def _polkit_agent_running() -> bool:
    """
    Heuristica simple: busca procesos de agentes de polkit conocidos.
    Sin uno corriendo, pkexec falla o se queda colgado esperando un
    dialogo que nunca aparece (comun en Hyprland, que no trae uno por
    defecto a diferencia de GNOME/KDE).
    """
    agent_names = (
        "polkit-gnome-authentication-agent-1",
        "polkit-kde-authentication-agent-1",
        "hyprpolkitagent",
        "lxpolkit",
        "xfce-polkit",
    )
    try:
        output = subprocess.run(
            ["ps", "-e", "-o", "comm="], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return True  # si no se puede chequear, no bloqueemos el flujo
    return any(name in output for name in agent_names)


def check() -> DependencyReport:
    report = DependencyReport()
    for binary in REQUIRED_BINARIES + OPTIONAL_BINARIES:
        checker = CUSTOM_BINARY_CHECKS.get(binary)
        found = checker() if checker else (shutil.which(binary) is not None)
        if not found:
            report.missing_binaries.append(binary)

    for module_name, pip_name in PYTHON_PACKAGES.items():
        if importlib.util.find_spec(module_name) is None:
            report.missing_python.append(pip_name)

    report.package_manager = detect_package_manager()
    report.polkit_agent_running = _polkit_agent_running()
    return report


def install_missing_system_packages(report: DependencyReport) -> None:
    """Instala los binarios faltantes con el gestor de paquetes detectado,
    via pkexec. Antes de instalar, sincroniza la base de paquetes de la
    distro (una sola autenticacion para las dos cosas) para evitar el
    error mas comun de todos: que la base local este desactualizada y
    apunte a una version de un paquete que los espejos ya no tienen."""
    if not report.missing_binaries:
        return
    if report.package_manager is None:
        raise DependencyError(
            "No se reconocio ningun gestor de paquetes (pacman/apt/dnf/zypper). "
            "Instala manualmente: " + ", ".join(report.missing_binaries)
        )
    if shutil.which("pkexec") is None:
        raise DependencyError(
            "No se encontro 'pkexec' en el PATH; no se puede pedir la "
            "contrasena en una ventana grafica para instalar paquetes."
        )
    sync_cmd = PACKAGE_SYNC_COMMANDS.get(report.package_manager)
    install_cmd = [*PACKAGE_MANAGERS[report.package_manager], *report._map_binaries_to_packages()]
    script = " && ".join(" ".join(part) for part in filter(None, [sync_cmd, install_cmd]))
    try:
        subprocess.run(
            ["pkexec", "sh", "-c", script],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 126:
            raise DependencyError(
                "Se cancelo la autenticacion (o no hay un agente de polkit "
                "corriendo -- en Hyprland instala y arranca 'hyprpolkitagent' "
                "o 'polkit-gnome')."
            ) from exc
        detail = exc.stderr.strip()
        if any(hint in detail.lower() for hint in _MIRROR_ERROR_HINTS):
            raise DependencyError(
                "Los espejos de paquetes no tienen (todavia) la version que "
                "tu sistema tiene registrada -- es un problema de la base de "
                "paquetes de tu distro, no de NexoHub ni de tu conexion. Se "
                "intento sincronizar antes de instalar, pero puede que haga "
                "falta una actualizacion completa. Abri una terminal y corre:\n\n"
                + (
                    "  sudo pacman -Syyu\n\n(el doble 'yy' fuerza a re-descargar "
                    "la base de datos completa, no solo la desactualizada)"
                    if report.package_manager == "pacman"
                    else "  sudo apt-get update && sudo apt-get upgrade"
                    if report.package_manager == "apt"
                    else "  sudo dnf upgrade --refresh"
                    if report.package_manager == "dnf"
                    else "  sudo zypper refresh && sudo zypper update"
                )
                + "\n\ny despues volve a intentar instalar desde aca.\n\n"
                f"Detalle tecnico:\n{detail}"
            ) from exc
        raise DependencyError(f"Fallo la instalacion: {detail}") from exc


def install_missing_python_packages(report: DependencyReport) -> None:
    """Instala los paquetes de Python faltantes con pip (sin privilegios de root)."""
    if not report.missing_python:
        return
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--break-system-packages",
                *report.missing_python,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise DependencyError(f"Fallo 'pip install': {exc.stderr}") from exc
