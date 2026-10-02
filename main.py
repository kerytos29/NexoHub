#!/usr/bin/env python3
"""
scrcpy-autoconfig: detecta tu setup y genera/corre el comando scrcpy
optimo automaticamente. Todo configurable desde config.yaml.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

from detectors import android_device, cpu, monitor, privileged, usb_topology, wireless
import command_builder

console = Console()
CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def apply_profile(config: dict, profile_name: str | None) -> dict:
    """
    Los "profiles" en config.yaml son bloques con las mismas claves que
    el nivel raiz (video_codec, bitrate_max, max_size, etc). Aplicar un
    perfil simplemente sobreescribe esas claves sobre la config base,
    sin tener que editar valores sueltos cada vez que cambias de juego.
    """
    if not profile_name:
        return config
    profiles = config.get("profiles", {})
    if profile_name not in profiles:
        console.print(
            f"[red]El perfil '{profile_name}' no existe en config.yaml.[/red] "
            f"Perfiles disponibles: {list(profiles.keys()) or 'ninguno'}"
        )
        sys.exit(1)
    merged = dict(config)
    merged.update(profiles[profile_name])
    return merged


def gather(config: dict):
    """Corre los cuatro detectores y devuelve sus resultados, o aborta con
    un mensaje claro si algo falta."""
    try:
        device = android_device.detect(
            serial=config.get("preferred_device_serial") or None
        )
    except android_device.AndroidDeviceError as exc:
        console.print(f"[red]Error detectando el celular:[/red] {exc}")
        sys.exit(1)

    try:
        usb = usb_topology.find_topology_by_serial(device.serial)
    except usb_topology.UsbTopologyError as exc:
        console.print(f"[yellow]Aviso USB:[/yellow] {exc}")
        usb = usb_topology.UsbTopology()

    try:
        monitors = monitor.detect_all()
        mon = monitor.pick_target(monitors, config.get("preferred_monitor") or None)
    except monitor.MonitorError as exc:
        console.print(f"[red]Error detectando monitor:[/red] {exc}")
        sys.exit(1)

    try:
        cpu_status = cpu.detect()
    except cpu.CpuError as exc:
        console.print(f"[yellow]Aviso CPU:[/yellow] {exc}")
        cpu_status = cpu.CpuStatus(governor="unknown", current_freq_mhz=0, max_freq_mhz=0)

    return device, usb, mon, cpu_status


def print_diagnosis(device, usb, mon, cpu_status) -> None:
    table = Table(title="Diagnostico de scrcpy-autoconfig")
    table.add_column("Componente")
    table.add_column("Valor")

    encoder = device.best_encoder
    conexion = f"WiFi ({device.serial})" if wireless.is_wireless_serial(device.serial) else "USB"
    table.add_row("Conexion", conexion)
    table.add_row("Celular", f"{device.brand} {device.model} (Android {device.android_version})")
    table.add_row("Chipset", device.chipset)
    table.add_row(
        "Mejor encoder",
        f"{encoder.name} ({encoder.codec}, {encoder.kind})" if encoder else "no detectado",
    )
    table.add_row("Velocidad USB", usb.speed_label)
    table.add_row("USB autosuspend", "si" if usb.any_autosuspend else "no")
    table.add_row("Monitor", f"{mon.name} {mon.width}x{mon.height}")
    table.add_row("CPU governor", cpu_status.governor)
    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(prog="scrcpy-autoconfig")
    parser.add_argument(
        "--auto", action="store_true", help="Detecta todo y corre scrcpy directamente."
    )
    parser.add_argument(
        "--diagnose", action="store_true", help="Solo muestra el diagnostico, no corre nada."
    )
    parser.add_argument(
        "--print-command", action="store_true", help="Solo imprime el comando, no lo ejecuta."
    )
    parser.add_argument(
        "--fix-usb-power", action="store_true", help="Fuerza power/control=on en la cadena USB."
    )
    parser.add_argument(
        "--fix-cpu-governor", action="store_true", help="Cambia el CPU governor a 'performance'."
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Nombre de un perfil definido en config.yaml (ej. competitive, casual).",
    )
    parser.add_argument(
        "--wifi",
        action="store_true",
        help=(
            "Cambia el celular (conectado por USB) a modo WiFi/LAN antes de "
            "armar el comando. El celular y el PC deben estar en la misma red."
        ),
    )
    parser.add_argument(
        "--wifi-port",
        type=int,
        default=wireless.DEFAULT_PORT,
        help=f"Puerto TCP a usar en modo WiFi (default: {wireless.DEFAULT_PORT}).",
    )
    args = parser.parse_args()

    if not any([args.auto, args.diagnose, args.print_command]):
        parser.print_help()
        sys.exit(0)

    config = load_config()
    config = apply_profile(config, args.profile)
    device, usb, mon, cpu_status = gather(config)

    if args.wifi:
        try:
            address = wireless.enable_over_wifi(device.serial, port=args.wifi_port)
        except wireless.WirelessError as exc:
            console.print(f"[red]No se pudo activar el modo WiFi:[/red] {exc}")
            sys.exit(1)
        console.print(f"[green]Conectado por WiFi en:[/green] {address}")
        console.print("Ya puedes desconectar el cable USB.")
        # Se re-detecta el celular usando la nueva direccion como serial:
        # la topologia USB ya no aplica (command_builder usa un bitrate
        # conservador propio para WiFi), pero el resto (chipset, encoders)
        # se vuelve a leer igual, ahora via la conexion inalambrica.
        try:
            device = android_device.detect(serial=address)
        except android_device.AndroidDeviceError as exc:
            console.print(f"[red]Error leyendo el celular por WiFi:[/red] {exc}")
            sys.exit(1)
        usb = usb_topology.UsbTopology()

    fix_usb = args.fix_usb_power or config.get("auto_fix_usb_power")
    fix_cpu = args.fix_cpu_governor or config.get("auto_fix_cpu_governor")
    if fix_usb or fix_cpu:
        try:
            touched = privileged.apply_privileged_fixes(usb, fix_usb, fix_cpu)
            if touched:
                console.print(f"[green]USB power forzado a 'on' en:[/green] {touched}")
            if fix_cpu:
                console.print("[green]CPU governor cambiado a 'performance'.[/green]")
                cpu_status = cpu.detect()
        except privileged.PrivilegedActionError as exc:
            console.print(f"[red]No se pudieron aplicar los ajustes con privilegios:[/red] {exc}")

    if args.diagnose:
        print_diagnosis(device, usb, mon, cpu_status)
        return

    result = command_builder.build(device, usb, mon, cpu_status, overrides=config)

    for warning in result.warnings:
        console.print(f"[yellow]⚠[/yellow] {warning}")

    command = result.as_command()
    console.print(f"[bold]Comando generado:[/bold] {' '.join(command)}")

    if args.print_command:
        return

    console.print("[bold green]Lanzando scrcpy...[/bold green]")
    subprocess.run(command, check=False)


if __name__ == "__main__":
    main()
