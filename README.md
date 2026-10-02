# scrcpy-autoconfig

Detecta tu celular Android, tu topología USB, tu monitor y tu CPU, y genera
automáticamente el comando `scrcpy` óptimo para tu setup — todo editable
desde `config.yaml` sin tocar código.

## Estado actual (v1, en desarrollo)

1. `detectors/android_device.py` — modelo, chipset, encoders vía ADB/scrcpy
2. `detectors/usb_topology.py` — velocidad negociada real y power/control vía pyudev
3. `detectors/monitor.py` — resolución vía screeninfo
4. `detectors/cpu.py` — governor vía psutil/cpupower
5. `command_builder.py` — reglas fijas (aún no es un motor de reglas genérico)
6. `detectors/dependencies.py` — chequeo e instalación automática de dependencias (multi-distro, vía pkexec)
7. `gui.py` — pestaña de Dependencias + contraseña gráfica (pkexec en vez de sudo en terminal)
8. FPS en vivo: `--max-fps` configurable, `--print-fps` parseado desde `gui.py` (leído por línea vía `stdbuf`), overlay estilo BlueStacks (`gtk-layer-shell`, con respaldo en Qt si no está instalado) y botón "Detener transmisión"
9. `detectors/wireless.py` — modo WiFi/LAN (`adb tcpip`): el celular se puede usar sin cable una vez detectado por USB una vez
10. `detectors/bluetooth.py` — audio del juego por Bluetooth directo celular→PC (cero impacto en video/control) y audio "dual" por WiFi en paralelo (video/control por USB, audio por una segunda instancia de scrcpy vía WiFi)
11. X Motor de reglas genérico + base de chipsets por fabricante
12. X Multiplataforma (Windows/macOS), telemetría opt-in, empaquetado AUR/Flatpak
13. X Mapeador de teclado y mouse para no insalatar apps de terceros en el dispositvo movil. 

## Instalación

**Opción 1 (recomendada): pestaña "Dependencias" de la GUI.** Verifica
binarios del sistema y paquetes de Python, y los instala con un botón
(pide la contraseña en una ventana gráfica vía `pkexec`, nunca en
terminal). Detecta pacman/apt/dnf/zypper automáticamente.

**Opción 2: manual (Arch Linux)**

```bash
sudo pacman -S scrcpy android-tools cpupower polkit python python-pip
pip install -r requirements.txt --break-system-packages
```

`polkit` es lo que provee `pkexec`. En Hyprland (a diferencia de
GNOME/KDE) no viene un agente gráfico de polkit por defecto: instala y
arranca uno junto con tu sesión, por ejemplo `hyprpolkitagent` o
`polkit-gnome`, o `pkexec` no podrá mostrar el diálogo de contraseña.

## ¿Funciona con cualquier celular?

Sí: todo lo específico del chipset Android (nombre del encoder de video) se
detecta en vivo vía `scrcpy --list-encoders`, nunca se asume un
fabricante. Los perfiles de `config.yaml` solo fijan el códec deseado
(h264/h265); el encoder exacto (MediaTek, Qualcomm, Exynos, Unisoc...)
se resuelve según el celular conectado en ese momento.

## Uso

```bash
# Solo ver el diagnóstico, sin correr nada
python main.py --diagnose

# Generar y mostrar el comando sin ejecutarlo
python main.py --print-command

# Detectar todo y lanzar scrcpy directamente
python main.py --auto

# Además, arreglar USB power y CPU governor antes de lanzar
python main.py --auto --fix-usb-power --fix-cpu-governor

# Cambiar el celular a WiFi/LAN antes de armar el comando (requiere
# estar conectado por USB primero; el celular y el PC deben estar en
# la misma red)
python main.py --auto --wifi
python main.py --auto --wifi --wifi-port 5566   # puerto TCP custom
```

## WiFi / LAN (sin cable)

Una vez que el celular fue detectado por USB al menos una vez, se puede
seguir usando sin cable:

- **GUI**: botón "Conectar por WiFi" al lado de "Detectar", en la
  tarjeta "Dispositivo".
  Activa `adb tcpip`, conecta por la IP del celular y ya se puede
  desenchufar el cable. El botón cambia a "Volver a USB" para cerrar
  esa conexión cuando se quiera.
- **CLI**: flag `--wifi` (ver arriba).

El celular y el PC tienen que estar en la **misma red WiFi/LAN** (no
sirve por internet). Espera algo más de latencia y variabilidad que por
cable — no se recomienda para uso competitivo, pero funciona bien para
mirar/controlar el celular sin estar atado al cable.

> Nota: `adb` corre como un demonio separado de la app. Si cierras
> NexoHub sin volver a USB, la conexión WiFi puede quedar "colgada" en
> `adb devices` — la app la detecta y la limpia sola la próxima vez que
> se detecte por USB, así que no hace falta hacer nada manual.

## Instalación

```bash
./install.sh
```

Copia el proyecto a `~/.local/share/nexohub` (sin pisar tu `config.yaml`
si ya existía uno), instala las dependencias de Python con pip para tu
usuario, y crea un lanzador `nexohub` + una entrada de menú ("NexoHub")
para que aparezca en rofi/wofi/el launcher que uses. Las dependencias
de *sistema* (adb, scrcpy, cpupower, polkit) las instala la propia app
la primera vez que la abres, desde la pestaña "Dependencias".

Para desinstalar: `./uninstall.sh` (o `./install.sh --uninstall`, es lo mismo).

## Configuración

Edita `config.yaml`. Cualquier valor que pongas ahí (codec, encoder,
bitrate, tamaño máximo, FPS máximo, overlay de FPS, ruta de audio,
flags extra, auto-fix de USB/CPU) sobreescribe lo que el programa
detecta automáticamente. Déjalo vacío para que todo se detecte solo.

## FPS en vivo, overlay y detener transmisión

En la tarjeta "Transmisión" de la GUI:

- **FPS máximo de transmisión**: agrega `--max-fps=N` al comando (0 =
  sin límite). Se guarda en `config.yaml` como `max_fps`.
- **Overlay de FPS en pantalla**: activa `--print-fps` internamente y
  parsea las líneas `INFO: N fps` que imprime scrcpy (leídas por línea
  gracias a `stdbuf -oL -eL`, para que lleguen al instante y no en
  bloques). Se muestran en una placa flotante estilo BlueStacks/
  emulador, fija en una esquina y garantizada por encima de todo
  (incluida la ventana de scrcpy en pantalla completa): usa el
  protocolo `wlr-layer-shell` vía `gtk-layer-shell` (dependencia
  opcional del sistema, instalable desde la página "Dependencias"). Si
  no está instalada, cae de vuelta a una ventana Qt común (funciona en
  X11; en Wayland/Hyprland puede no quedar fija ni "siempre encima").
  Se guarda como `fps_overlay`.
- Si el FPS o el bitrate configurado supera un umbral razonable para
  USB 2.0/WiFi (60 FPS / 20 Mbps), aparece un aviso: la calidad va a
  mejorar, pero el delay o la fluidez pueden verse afectados.
- **Detener transmisión**: corta el proceso `scrcpy` (SIGTERM, y si no
  responde en 4s, `SIGKILL`) sin cerrar NexoHub, para volver a lanzar
  con otros ajustes sin reiniciar la app.

## Resolución del celular (ADB), aparte de la de transmisión

En la tarjeta "Dispositivo" de la GUI, sección plegable "Resolución
del celular (ADB)":

- Cambia la resolución REAL de la pantalla del celular con
  `adb shell wm size ANCHOxALTO` — esto es distinto de "Max size (px)"
  de Opciones avanzadas, que solo reescala la imagen ya capturada para
  la PC sin tocar lo que Android cree que mide su propia pantalla.
  Sirve para que el juego renderice directo al tamaño final (algunos
  ajustan su render interno a la resolución que reporta el sistema).
- **"Usar la de mi monitor"** llena los campos invirtiendo la
  resolución del monitor detectado a vertical (ej. monitor 1920x1080 →
  celular 1080x1920) sin aplicar nada todavía.
- **"Aplicar"** manda el cambio al celular; **"Restablecer a nativa"**
  vuelve a la resolución de fábrica (`adb shell wm size reset`) en
  cualquier momento.
- Advertencia: una resolución no nativa puede hacer que lanzadores/
  apps del sistema (y algunos juegos, que la detectan como intento de
  ventaja) se vean o se comporten raro. No se guarda en `config.yaml`:
  se aplica al celular directamente y dura hasta el próximo reinicio o
  hasta que se restablezca.

## Audio: 3 rutas, para evitar el lag en competitivo

Si el audio del celular comparte el mismo túnel USB/ADB que el video y
el control, el celular tiene que codificar audio **y** video al mismo
tiempo por el mismo cable — en USB 2.0 (o un chipset ya exigido por el
juego) eso alcanza para meter delay en el input aunque el audio en sí
suene bien. El combo "Audio" de la tarjeta "Transmisión" tiene 4
opciones:

1. **Apagado (sin audio)** — la más segura: no toca para nada el túnel
   de video/control, así que no puede meter lag. Se escucha el sonido
   directo del celular (parlante, cable o auriculares conectados a él).
2. **Por USB, junto con video** — audio normal de scrcpy, mismo túnel.
   Si se nota lag con esta opción, en "Opciones avanzadas" prueba en
   este orden: **Audio codec → `aac`** (suele tener encoder de hardware,
   `opus` suele ser software y compite por CPU con el video), **Audio
   bitrate → `64K`**, y bajar el **Audio buffer** de a poco (50→30→15ms).
3. **Bluetooth (externo, recomendado)** — el celular se empareja
   directo con el PC por Bluetooth y, en Android, se elige el PC como
   salida de audio del juego (como unos parlantes Bluetooth más). El
   audio nunca pasa por scrcpy: cero impacto en el cable ni en
   video/input. Contras: ~100-150ms de desfasaje audio/video típico del
   códec SBC de A2DP (baja bastante con aptX Low Latency o LC3 si el
   celular y el adaptador BT del PC lo soportan). El botón "🎧
   Bluetooth" al lado del combo muestra si el PC tiene adaptador y qué
   dispositivos ya tiene emparejados — el emparejamiento en sí se hace
   desde el applet de Bluetooth del sistema, no desde NexoHub.
4. **WiFi en paralelo (dual, menor lag)** — video y control siguen por
   USB (sin audio en ese comando) y una segunda instancia de scrcpy,
   aparte, trae *solo* el audio por WiFi — un canal de radio
   completamente distinto del cable, así que no compite con el
   video/input. Corre invisible (`--no-window`, no hay nada que mirar).
   Requiere Android 11+ y que el celular ya haya sido detectado por
   USB; al elegir esta opción la GUI prepara sola el canal de WiFi para
   el audio (separado del canal principal de video/control). Android va
   a pedir confirmar un permiso en pantalla la primera vez que arranca
   cada instancia.

Si nada de esto alcanza, revisa el cable/puerto USB: NexoHub avisa en
el diagnóstico si detecta USB 2.0 (menos margen para audio+video que
USB 3.x).

## Licencia

MIT. Consulta el archivo [LICENSE](LICENSE) para el texto completo.

