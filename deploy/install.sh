#!/usr/bin/env bash
# Instala o actualiza los servicios de grabación en el mini PC de la mesa.
# Uso (desde la raíz del repositorio):  sudo ./deploy/install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Ejecuta este script con sudo." >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/billar
CONFIG=/etc/billar/billar.toml

echo "==> Paquetes del sistema"
apt-get update -q
apt-get install -y -q ffmpeg python3-venv smartmontools

echo "==> Usuario de servicio 'billar'"
if ! id billar >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/billar --shell /usr/sbin/nologin billar
fi
install -d -o billar -g billar -m 0750 /var/lib/billar
install -d -o billar -g billar -m 0750 /srv/billar/video
install -d -m 0755 /etc/billar

echo "==> Programa en $PREFIX"
install -d "$PREFIX"
rm -rf "$PREFIX/src"
cp -r "$REPO_DIR/edge" "$PREFIX/src"
if [[ ! -x "$PREFIX/venv/bin/python" ]]; then
  python3 -m venv "$PREFIX/venv"
fi
"$PREFIX/venv/bin/pip" install -q --no-deps "$PREFIX/src"

echo "==> Configuración"
if [[ ! -f "$CONFIG" ]]; then
  install -m 0640 -g billar "$REPO_DIR/deploy/billar.example.toml" "$CONFIG"
  echo "    Creado $CONFIG: edita la URL y la contraseña de la cámara antes de seguir."
fi
sudo -u billar BILLAR_CONFIG="$CONFIG" "$PREFIX/venv/bin/billar" check-config

echo "==> Servicios"
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-recorder@.service /etc/systemd/system/
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-health.service /etc/systemd/system/
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-retention.service /etc/systemd/system/
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-retention.timer /etc/systemd/system/
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-tmpfiles.conf /etc/tmpfiles.d/billar.conf
install -d /etc/systemd/system.conf.d
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-watchdog.conf /etc/systemd/system.conf.d/billar-watchdog.conf
# Registro del sistema persistente y acotado, para revisar qué pasó tras un corte.
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=500M\n' > /etc/systemd/journald.conf.d/billar.conf

systemd-tmpfiles --create /etc/tmpfiles.d/billar.conf
systemctl daemon-reload
systemctl daemon-reexec   # aplica el watchdog de hardware
systemctl enable --now billar-health.service billar-retention.timer
for cam in $(sudo -u billar BILLAR_CONFIG="$CONFIG" "$PREFIX/venv/bin/billar" cameras); do
  systemctl enable "billar-recorder@${cam}.service"
  systemctl restart "billar-recorder@${cam}.service"
done

echo
echo "Listo. La grabación arranca sola con cada encendido."
echo "  Estado:   billar --config $CONFIG status"
echo "  Eventos:  billar --config $CONFIG events"
echo "Recuerda activar en la BIOS: 'Restore on AC Power Loss = Power On'."
