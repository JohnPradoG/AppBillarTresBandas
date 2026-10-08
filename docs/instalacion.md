# Instalación del equipo de una mesa

## 1. BIOS del mini PC

- **Restore on AC Power Loss** (o "After Power Failure"): **Power On**. Así el equipo enciende solo cuando vuelve la luz.
- Arranque desde el disco interno; desactivar el arranque por red.
- Si existe, activar el watchdog de hardware (en los N100 suele estar activo por defecto).

## 2. Ubuntu Server 24.04 LTS

Instalación mínima, sin escritorio. Particiones recomendadas en el SSD de 1 TB:

| Partición | Tamaño | Montaje | Uso |
| --- | --- | --- | --- |
| EFI | 1 GB | `/boot/efi` | arranque |
| Sistema | 60 GB | `/` | Ubuntu, programa, base de datos |
| Video | el resto | `/srv/billar/video` | grabaciones |

La partición de video va en `/etc/fstab` con `noatime`, por ejemplo:

```
UUID=<uuid-de-la-particion>  /srv/billar/video  ext4  defaults,noatime,nofail  0  2
```

`nofail` deja que el sistema arranque aunque el disco de video falle; en ese caso la pantalla muestra ERROR DE ALMACENAMIENTO y no se graba en el disco del sistema (`require_mount = true`).

## 3. Red

- Cámara y mini PC conectados por cable (inyector PoE para una mesa, switch PoE para varias).
- Dar a la cámara una IP fija (por ejemplo 192.168.10.64) y cambiar su contraseña de fábrica.
- La cámara no necesita Internet.

## 4. Programa

Ver el README: `sudo ./deploy/install.sh`, editar `/etc/billar/billar.toml` y volver a ejecutarlo.

## 5. Comprobar

```bash
billar --config /etc/billar/billar.toml status   # GRABANDO
billar --config /etc/billar/billar.toml events   # registro de eventos
ls /srv/billar/video/<camara>/                   # un archivo por minuto
```

Prueba de corte de luz: desenchufar el equipo, volver a enchufarlo y comprobar sin tocar nada que vuelve a GRABANDO y que `events` muestra "apagado inesperado" y "Grabación iniciada automáticamente".
