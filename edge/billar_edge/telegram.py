"""Bot de Telegram para compartir jugadas (opcional, gratis).

El QR de la pantalla abre https://t.me/<bot>?start=<enlace>; al tocar
Iniciar, Telegram le manda al bot "/start <enlace>" y el bot responde con el
video. Usa la API de bots por consulta larga (getUpdates), así que no
necesita abrir puertos ni dominio: solo salida a Internet. Si no hay
Internet, espera y reintenta; el resto del sistema no depende de esto.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import plays, share
from .config import Config
from .db import connect

log = logging.getLogger("billar.telegram")

API = "https://api.telegram.org/bot{token}/{method}"
POLL_SECONDS = 50


class Bot:
    def __init__(self, cfg: Config, opener=urllib.request.urlopen):
        self.cfg = cfg
        self.open = opener
        self.offset = 0

    def call(self, method: str, data: dict | None = None, files: dict | None = None, timeout: float = 70):
        url = API.format(token=self.cfg.telegram_token, method=method)
        if files:
            body, ctype = multipart(data or {}, files)
        else:
            body, ctype = json.dumps(data or {}).encode(), "application/json"
        req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype})
        with self.open(req, timeout=timeout) as r:
            out = json.loads(r.read())
        if not out.get("ok"):
            raise RuntimeError(out.get("description", "error de Telegram"))
        return out["result"]

    def poll_once(self) -> int:
        updates = self.call("getUpdates", {"offset": self.offset, "timeout": POLL_SECONDS,
                                           "allowed_updates": ["message"]}, timeout=POLL_SECONDS + 20)
        for u in updates:
            self.offset = u["update_id"] + 1
            msg = u.get("message") or {}
            text = (msg.get("text") or "").strip()
            chat = (msg.get("chat") or {}).get("id")
            if chat is None:
                continue
            try:
                self.handle(chat, text)
            except Exception:  # un mensaje malo no detiene al bot
                log.exception("No se pudo responder en Telegram")
        return len(updates)

    def handle(self, chat: int, text: str) -> None:
        parts = text.split(maxsplit=1)
        token = parts[1] if len(parts) == 2 and parts[0] == "/start" else None
        if token is None:
            self.call("sendMessage", {"chat_id": chat, "text":
                      "Hola. Para recibir una jugada, toca COMPARTIR en la pantalla de la mesa y escanea "
                      "el código de Telegram."})
            return
        conn = connect(self.cfg.db_path)
        try:
            row = share.valid(conn, token)
            play = plays.get(conn, row["play_id"]) if row else None
            if row is None or play is None or not Path(row["path"]).exists():
                self.call("sendMessage", {"chat_id": chat, "text":
                          "Ese enlace ya venció. Pide que vuelvan a compartir la jugada desde la pantalla."})
                return
            top, bottom = share.caption_lines(self.cfg, play)
            self.call("sendChatAction", {"chat_id": chat, "action": "upload_video"})
            with open(row["path"], "rb") as f:
                self.call("sendVideo", {"chat_id": str(chat), "caption": f"{top}\n{bottom}\nVano Systems",
                                        "supports_streaming": "true"},
                          files={"video": (f"jugada-{row['token'][:8]}.mp4", f.read(), "video/mp4")},
                          timeout=180)
            conn.execute("UPDATE shares SET telegram_sends = telegram_sends + 1 WHERE token = ?", (row["token"],))
            log.info("Jugada enviada por Telegram (enlace %s…)", row["token"][:6])
        finally:
            conn.close()


def multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    for k, (name, data, ctype) in files.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def main(cfg: Config) -> int:
    if not cfg.telegram_token or not cfg.telegram_bot:
        log.info("Telegram no está configurado ([share] telegram_token y telegram_bot); el bot no arranca.")
        return 0
    bot = Bot(cfg)
    wait = 5
    log.info("Bot de Telegram @%s en marcha", cfg.telegram_bot)
    while True:
        try:
            bot.poll_once()
            wait = 5
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as e:
            # Sin Internet o Telegram caído: reintentar sin molestar al resto.
            log.warning("Telegram no responde (%s); reintento en %s s", e, wait)
            time.sleep(wait)
            wait = min(wait * 2, 300)
