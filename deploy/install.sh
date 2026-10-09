#!/usr/bin/env bash
# Instala o actualiza los servicios de la mesa (grabación y pantalla) en el mini PC.
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
apt-get install -y -q ffmpeg python3-venv smartmontools curl cage
# Navegador de la pantalla: Google Chrome (gratuito, reproduce H.264 con
# aceleración por hardware). Se instala desde el repositorio oficial de Google.
if ! command -v google-chrome-stable >/dev/null 2>&1; then
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list
  apt-get update -q
  apt-get install -y -q google-chrome-stable
fi

echo "==> Usuario de servicio 'billar'"
if ! id billar >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/billar --shell /usr/sbin/nologin billar
fi
install -d -o billar -g billar -m 0750 /var/lib/billar
install -d -o billar -g billar -m 0750 /srv/billar/video
install -d -m 0755 /etc/billar
# Usuario sin privilegios para el navegador de la pantalla táctil.
if ! id billar-kiosko >/dev/null 2>&1; then
  useradd --create-home --home-dir /var/lib/billar-kiosko --shell /usr/sbin/nologin billar-kiosko
fi
usermod -aG video,render,input billar-kiosko

echo "==> Programa en $PREFIX"
install -d "$PREFIX"
rm -rf "$PREFIX/src"
cp -r "$REPO_DIR/edge" "$PREFIX/src"
if [[ ! -x "$PREFIX/venv/bin/python" ]]; then
  python3 -m venv "$PREFIX/venv"
fi
"$PREFIX/venv/bin/pip" install -q --no-deps "$PREFIX/src"
install -d "$PREFIX/bin"
install -m 0755 "$REPO_DIR/deploy/bin/billar-navegador" "$PREFIX/bin/"

echo "==> Configuración"
if [[ ! -f "$CONFIG" ]]; then
  install -m 0640 -g billar "$REPO_DIR/deploy/billar.example.toml" "$CONFIG"
  echo "    Creado $CONFIG: edita la URL y la contraseña de la cámara antes de seguir."
fi
sudo -u billar BILLAR_CONFIG="$CONFIG" "$PREFIX/venv/bin/billar" check-config

echo "==> Servicios"
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-recorder@.service /etc/systemd/system/
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-health.service /etc/systemd/system/
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-live@.service /etc/systemd/system/
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-ui.service /etc/systemd/system/
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-share.service /etc/systemd/system/
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-telegram.service /etc/systemd/system/
install -m 0644 "$REPO_DIR"/deploy/systemd/billar-kiosk.service /etc/systemd/system/
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
systemctl enable billar-ui.service
systemctl restart billar-ui.service
systemctl enable billar-share.service billar-telegram.service
systemctl restart billar-share.service billar-telegram.service
for cam in $(sudo -u billar BILLAR_CONFIG="$CONFIG" "$PREFIX/venv/bin/billar" cameras); do
  systemctl enable "billar-recorder@${cam}.service" "billar-live@${cam}.service"
  systemctl restart "billar-recorder@${cam}.service" "billar-live@${cam}.service"
done
# La pantalla táctil ocupa tty1. Sin pantalla conectada no hace daño.
systemctl disable getty@tty1.service 2>/dev/null || true
systemctl enable billar-kiosk.service
systemctl restart billar-kiosk.service

echo
echo "Listo. La grabación y la pantalla arrancan solas con cada encendido."
echo "  Estado:   billar --config $CONFIG status"
echo "  Eventos:  billar --config $CONFIG events"
echo "Recuerda activar en la BIOS: 'Restore on AC Power Loss = Power On'."
