"""Lee y opcionalmente ajusta el CPU governor del host."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


@dataclass
class CpuStatus:
    governor: str
    current_freq_mhz: float
    max_freq_mhz: float


class CpuError(RuntimeError):
    pass


def detect() -> CpuStatus:
    if psutil is None:
        raise CpuError("Falta la libreria psutil. Instalala con: pip install psutil")
    freq = psutil.cpu_freq()
    if freq is None:
        raise CpuError("No se pudo leer la frecuencia de CPU en este sistema.")

    governor = "unknown"
    try:
        with open(
            "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", encoding="utf-8"
        ) as fh:
            governor = fh.read().strip()
    except OSError:
        pass

    return CpuStatus(
        governor=governor,
        current_freq_mhz=freq.current,
        max_freq_mhz=freq.max,
    )


def set_performance_governor() -> None:
    """
    Cambia el governor a 'performance' con cpupower. Requiere privilegios
    de root: se piden con pkexec, que muestra un dialogo grafico nativo
    (via el agente de polkit) en vez de bloquear esperando una contrasena
    en una terminal que en una app grafica no existe.
    """
    if shutil.which("cpupower") is None:
        raise CpuError(
            "No se encontro 'cpupower' en el PATH. Instalalo (paquete: cpupower)."
        )
    if shutil.which("pkexec") is None:
        raise CpuError(
            "No se encontro 'pkexec' en el PATH. Instalalo (paquete: polkit) "
            "para poder pedir la contrasena en una ventana grafica."
        )
    try:
        subprocess.run(
            ["pkexec", "cpupower", "frequency-set", "-g", "performance"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 126:
            raise CpuError(
                "Se cancelo la autenticacion (o no hay un agente de polkit "
                "corriendo -- en Hyprland instala y arranca 'hyprpolkitagent' "
                "o 'polkit-gnome')."
            ) from exc
        raise CpuError(f"No se pudo cambiar el governor: {exc.stderr}") from exc
