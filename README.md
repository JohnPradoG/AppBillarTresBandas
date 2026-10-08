# AppBillarTresBandas

Sistema para mesas de billar a tres bandas: grabación continua 24/7, pantalla con retraso, repetición instantánea, historial de 7 días, jugadas protegidas y marcador. Funciona sin Internet. Desarrollado por Vano Systems.

- Arquitectura aprobada: [documento de arquitectura](https://claude.ai/code/artifact/f57a7247-8ce0-4d0f-b59c-47cdc0c712d2)
- Estado: **Fase 1, grabación básica** (este código).

## Qué hace la Fase 1

Cada mesa tiene un mini PC con Ubuntu Server 24.04 y una cámara IP PoE. Al encender el equipo:

1. systemd arranca los servicios solos (sin botón de "Grabar").
2. El servicio de grabación repara el índice si hubo un corte de luz, espera a la cámara y empieza a grabar.
3. FFmpeg copia el video de la cámara sin recomprimir en segmentos de 1 minuto (MP4 fragmentado, nombre = hora UTC de inicio). Un corte de luz pierde como máximo el último segundo.
4. Cada segmento cerrado queda registrado en SQLite con su huella SHA-256.
5. El monitor de salud revisa cada 5 s que el archivo crece, que la imagen no está negra ni congelada y el estado del disco, y publica el indicador (GRABANDO, CÁMARA DESCONECTADA, SIN SEÑAL, GRABACIÓN DETENIDA, ERROR DE ALMACENAMIENTO).
6. Cada 10 minutos la limpieza borra lo que pasa de 7 días, nunca los segmentos protegidos. Si el disco se llena, borra antes de tiempo los más antiguos para no dejar de grabar.
7. Si algo se cuelga: el supervisor reinicia FFmpeg, systemd reinicia el supervisor y el watchdog de hardware reinicia el equipo.

## Estructura

| Carpeta | Contenido |
| --- | --- |
| `edge/billar_edge/` | Servicios de la mesa en Python 3.12, sin dependencias externas |
| `edge/tests/` | Pruebas, incluida una de punta a punta con una cámara simulada |
| `deploy/` | Instalador, configuración de ejemplo y servicios de systemd |
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

Las pruebas necesitan `ffmpeg` y `ffprobe` instalados.
