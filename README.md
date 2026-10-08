# AppBillarTresBandas

Sistema para mesas de billar a tres bandas: grabación continua 24/7, pantalla con retraso, repetición instantánea, historial de 7 días, jugadas protegidas y marcador. Funciona sin Internet. Desarrollado por Vano Systems.

- Arquitectura aprobada: [documento de arquitectura](https://claude.ai/code/artifact/f57a7247-8ce0-4d0f-b59c-47cdc0c712d2)
- Estado: **Fase 2, pantalla con retraso** (Fase 1, grabación básica, incluida).

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
7. REPETICIÓN y GUARDAR JUGADA ya están en pantalla y avisan que llegan en las Fases 3 y 5.

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
