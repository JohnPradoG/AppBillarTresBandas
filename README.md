# AppBillarTresBandas

Sistema para mesas de billar a tres bandas: grabación continua 24/7, pantalla con retraso, repetición instantánea, historial de 7 días, jugadas protegidas y marcador. Funciona sin Internet. Desarrollado por Vano Systems.

- Arquitectura aprobada: [documento de arquitectura](https://claude.ai/code/artifact/f57a7247-8ce0-4d0f-b59c-47cdc0c712d2)
- Estado: **Fase 4, historial** (incluye las Fases 1 a 3).

## Qué hace la Fase 1

Cada mesa tiene un mini PC con Ubuntu Server 24.04 y una cámara IP PoE. Al encender el equipo:

1. systemd arranca los servicios solos (sin botón de "Grabar").
2. El servicio de grabación repara el índice si hubo un corte de luz, espera a la cámara y empieza a grabar.
3. FFmpeg copia el video de la cámara sin recomprimir en segmentos de 1 minuto (MP4 fragmentado, nombre = hora UTC de inicio). Un corte de luz pierde como máximo el último segundo.
4. Cada segmento cerrado queda registrado en SQLite con su huella SHA-256.
5. El monitor de salud revisa cada 5 s que el archivo crece, que la imagen no está negra ni congelada y el estado del disco, y publica el indicador (GRABANDO, CÁMARA DESCONECTADA, SIN SEÑAL, GRABACIÓN DETENIDA, ERROR DE ALMACENAMIENTO).
6. Cada 10 minutos la limpieza borra lo que pasa de 7 días, nunca los segmentos protegidos. Si el disco se llena, borra antes de tiempo los más antiguos para no dejar de grabar.
7. Si algo se cuelga: el supervisor reinicia FFmpeg, systemd reinicia el supervisor y el watchdog de hardware reinicia el equipo.

## Qué hace la Fase 2

La pantalla táctil muestra la vista Mesa del mockup aprobado: un jugador a cada lado con su marcador, el video en el centro, TIEMPO DE PARTIDA y TIEMPO PARA TACAR arriba, y el indicador de grabación.

1. Un servicio aparte (`billar-live@<cámara>`) abre una segunda conexión a la cámara y deja los últimos 75 s en memoria como HLS, en trozos de 1 s y sin recomprimir. Si falla, la grabación no se entera.
2. Un servidor local (`billar-ui`, solo biblioteca estándar de Python) sirve la pantalla, la señal y una API pequeña.
3. Google Chrome en modo kiosco, dentro de `cage`, abre la pantalla al encender. Si se cierra, vuelve a abrirse solo.
4. La imagen va exactamente N segundos detrás de la cámara (10, 20 por defecto, 30, 45 o 60, desde el menú). El retraso se mide con el reloj del equipo y se corrige solo: salta si va muy atrasada, espera si va adelantada y acelera o frena un 8 % en desviaciones pequeñas.
5. Marcador: el jugador que toca su panel toma el turno, y cada toque reinicia el reloj para tacar (40 s). Suma con +, −, series rápidas de 2 a 5; calcula entradas, serie mayor y promedio. Por ahora se guarda en el navegador; en la Fase 6 pasa a la base de datos con jugadores y partidas.
6. Modo reposo: tras 20 minutos sin tocar la pantalla (`idle_minutes` en `[ui]`) aparecen la hora, el nombre del billar, la mesa y la marca Vano Systems, y la tarjeta cambia de lugar cada minuto para no marcar la pantalla. La grabación sigue igual. El toque que la despierta no marca carambolas.
7. GUARDAR JUGADA ya está en pantalla y avisa que llega en la Fase 5.

## Qué hace la Fase 3

1. Al pulsar REPETICIÓN, la pantalla toma el momento que estaba mostrando (hora de la pulsación menos el retraso real) y pide el clip a `POST /api/replay`.
2. El servidor corta 30 s antes y 15 s después de los segmentos grabados, sin recomprimir, en memoria (`/run/billar/repeticiones`). Si con un retraso corto los 15 s de después aún no se han grabado, espera a que lo estén.
3. La repetición empieza 10 s antes de la jugada, porque se pulsa después de verla. Tiene pausa, velocidades 1x, 0,5x, 0,25x y 0,1x, avance y retroceso cuadro a cuadro (para ver si una bola toca a la otra, con la hora al milisegundo), saltos de ±1 s y ±5 s, barra de tiempo con la marca de la jugada, zoom hasta 6x con botones, doble toque o pellizco, y desplazamiento arrastrando.
4. Muestra la hora de la jugada, de quién era el turno y el marcador en ese momento.
5. VOLVER A LA PARTIDA regresa a la imagen en vivo con retraso, que nunca se detuvo. Si nadie toca la pantalla durante 60 s, vuelve sola.

## Qué hace la Fase 4

1. En el menú, "Historial de los últimos días" abre la búsqueda: se elige el día (hoy y los 7 anteriores), la hora y el minuto.
2. Cada hora muestra una barra con lo grabado y los huecos, y cada minuto se pinta verde (grabado), amarillo (con cortes) o gris (sin grabación, cámara desconectada o equipo apagado). Los datos salen del índice de segmentos (`GET /api/history?date=AAAA-MM-DD`) más el archivo que se está grabando.
3. Al tocar un minuto se abre en el mismo reproductor de la repetición (cámara lenta, cuadro a cuadro, zoom), con botones para pasar al minuto anterior o siguiente grabado. "VOLVER AL HISTORIAL" regresa a la búsqueda; sin tocar la pantalla durante 3 minutos, vuelve sola a la partida.

## Qué hace la Fase 5

- **GUARDAR JUGADA** en la partida, en la repetición y en el historial. Desde la partida guarda 30 s antes y 15 s después de lo que se ve en pantalla; desde la repetición, esa misma repetición; desde el historial, el tramo que se está viendo.
- La jugada guardada es una copia propia en `<recordings_dir>/jugadas/AAAA-MM/`, de solo lectura y con su huella SHA-256, con el marcador, el jugador en turno y el número de partida. Dura 30 días (`protected_days`, decidido por John) y luego la limpieza diaria la borra, para que el disco se renueve solo. `billar verify-plays` revisa que ninguna falte o haya cambiado.
- Sección **JUGADAS** (menú): cada REPETICIÓN queda en la lista mientras exista su grabación (7 días) y se puede proteger desde ahí. Filtros por fecha, hora, jugador, partida y solo protegidas; acceso directo al historial. Avisa cuando las jugadas guardadas pasan de `protected_quota_gb` (100 GB por defecto).
- Compartir por WhatsApp y Telegram llega en la Fase 7; los nombres de los jugadores y las partidas en la base de datos, en la Fase 6.

## Estructura

| Carpeta | Contenido |
| --- | --- |
| `edge/billar_edge/` | Servicios de la mesa en Python 3.12, sin dependencias externas |
| `edge/billar_edge/static/` | Pantalla táctil: HTML, CSS y JavaScript sin compilación; hls.js y fuentes Barlow incluidas para funcionar sin Internet |
| `edge/tests/` | Pruebas, incluida una de punta a punta con una cámara simulada |
| `deploy/` | Instalador, configuración de ejemplo, servicios de systemd y lanzador del navegador |
| `docs/` | Guías de instalación y de prueba de la cámara |

## Instalar en el mini PC

```bash
git clone https://github.com/JohnPradoG/AppBillarTresBandas.git
cd AppBillarTresBandas
sudo ./deploy/install.sh        # crea /etc/billar/billar.toml la primera vez
sudo nano /etc/billar/billar.toml  # URL y contraseña de la cámara
sudo ./deploy/install.sh        # aplica la configuración y arranca la grabación
billar --config /etc/billar/billar.toml status
```

Detalles del disco, la BIOS y la cámara: [docs/instalacion.md](docs/instalacion.md) y [docs/prueba-camara.md](docs/prueba-camara.md).

## Desarrollo

```bash
cd edge
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Las pruebas necesitan `ffmpeg` y `ffprobe` instalados. Las de la pantalla (retraso y marcador) usan Node:

```bash
node --test edge/tests/js/*.test.mjs
```

Para ver la pantalla en el computador de desarrollo: `billar --config <archivo> live <cámara>` y `billar --config <archivo> ui`, y abrir http://127.0.0.1:8080/ en Chrome.
