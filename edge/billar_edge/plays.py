"""Jugadas: cada REPETICIÓN queda anotada y GUARDAR JUGADA la protege.

Una jugada anotada solo apunta a la grabación, así que desaparece con ella a
los 7 días. Al protegerla se hace una copia propia del clip en la carpeta de
jugadas, de solo lectura y con su huella SHA-256, junto con el marcador, el
turno y la partida de ese momento. Esa copia dura `protected_days` (30 por
defecto) y luego la limpieza diaria la borra, para que el disco se renueve
solo; con 0 no se borra nunca.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from . import events, replay
from .config import Config
from .db import now_ms
from .ids import uuid7
from .media import probe_fps, sha256_file
DAY_MS = 86_400_000

SOURCES = ("repeticion", "pantalla", "historial")
TEXT_FIELDS = ("turn_player", "player1", "player2")
INT_FIELDS = ("game_number", "score1", "score2", "innings")


class PlayError(Exception):
    pass


def clean_meta(meta: dict | None) -> dict:
    """Solo los datos del marcador que se guardan, con tipos y largos sanos."""
    meta = meta or {}
    out = {}
    for k in TEXT_FIELDS:
        v = meta.get(k)
        out[k] = str(v)[:60] if v not in (None, "") else None
    gid = meta.get("game_id")
    out["game_id"] = str(gid)[:40] if gid else None
    for k in INT_FIELDS:
        try:
            out[k] = int(meta[k]) if meta.get(k) is not None else None
        except (TypeError, ValueError):
            out[k] = None
    return out


def record(conn: sqlite3.Connection, cfg: Config, camera_id: str, moment_ms: int, start_ms: int,
           end_ms: int, source: str, meta: dict | None = None) -> str:
    if source not in SOURCES:
        raise PlayError(f"Origen no válido: {source}")
    meta = clean_meta(meta)
    if meta["game_id"] and not conn.execute("SELECT 1 FROM games WHERE id = ?", (meta["game_id"],)).fetchone():
        meta["game_id"] = None
    play_id = uuid7()
    conn.execute(
        """INSERT INTO plays (id, camera_id, table_number, moment_ms, start_ms, end_ms, source,
               game_number, turn_player, player1, player2, score1, score2, innings, created_at, game_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (play_id, camera_id, cfg.camera(camera_id).table_number, moment_ms, start_ms, end_ms, source,
         meta["game_number"], meta["turn_player"], meta["player1"], meta["player2"],
         meta["score1"], meta["score2"], meta["innings"], now_ms(), meta["game_id"]),
    )
    return play_id


def get(conn: sqlite3.Connection, play_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM plays WHERE id = ?", (play_id,)).fetchone()


def protect(conn: sqlite3.Connection, cfg: Config, play_id: str, clip: Path | None = None) -> sqlite3.Row:
    """Copia el clip a la carpeta de jugadas. Si ya estaba protegida no hace nada."""
    row = get(conn, play_id)
    if row is None:
        raise PlayError("Esa jugada no existe.")
    if row["protected_at"]:
        return row
    if cfg.require_mount and not os.path.ismount(cfg.recordings_dir):
        raise PlayError("El disco de video no está disponible.")
    if clip is None or not clip.exists():
        # El clip ya no está en memoria: se corta de nuevo de la grabación.
        try:
            clip = replay.build_range(cfg, row["camera_id"], row["start_ms"], row["end_ms"], row["moment_ms"]).path
        except replay.NoRecording as e:
            raise PlayError(f"La grabación de esa jugada ya no está disponible. {e}") from e

    moment = datetime.fromtimestamp(row["moment_ms"] / 1000)
    dest_dir = cfg.plays_dir / moment.strftime("%Y-%m")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{moment.strftime('%Y%m%d-%H%M%S')}-mesa{row['table_number']}-{play_id[-6:]}.mp4"
    tmp = dest.with_suffix(".tmp")
    with open(clip, "rb") as src, open(tmp, "wb") as out:
        shutil.copyfileobj(src, out, 1 << 20)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(tmp, 0o444)
    os.replace(tmp, dest)
    _fsync_dir(dest_dir)
    digest = sha256_file(dest)
    conn.execute(
        "UPDATE plays SET protected_at = ?, path = ?, bytes = ?, sha256 = ?, fps = ? WHERE id = ?",
        (now_ms(), str(dest), dest.stat().st_size, digest, probe_fps(cfg.ffprobe, dest), play_id),
    )
    events.record(conn, "info", events.PLAY_SAVED,
                  f"Jugada guardada: mesa {row['table_number']}, {moment.strftime('%d/%m/%Y %H:%M:%S')}",
                  row["camera_id"], {"jugada": play_id})
    return get(conn, play_id)


def unprotect(conn: sqlite3.Connection, cfg: Config, play_id: str) -> sqlite3.Row:
    """Quita la protección: borra la copia y la jugada vuelve a depender de la
    grabación (desaparece cuando esta se borre). Solo para administradores."""
    row = get(conn, play_id)
    if row is None:
        raise PlayError("Esa jugada no existe.")
    if not row["protected_at"]:
        raise PlayError("Esa jugada no está protegida.")
    if row["path"]:
        path = Path(row["path"])
        if path.resolve().is_relative_to(cfg.plays_dir.resolve()):
            path.unlink(missing_ok=True)
    conn.execute("UPDATE plays SET protected_at = NULL, path = NULL, bytes = NULL, sha256 = NULL WHERE id = ?",
                 (play_id,))
    return get(conn, play_id)


def local_time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%d/%m/%Y %H:%M:%S")


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def visible_since(cfg: Config, now: int) -> int:
    """Las jugadas sin proteger se ven mientras exista su grabación."""
    return now - cfg.retention_days * DAY_MS


def search(conn: sqlite3.Connection, cfg: Config, now: int, day_start: int | None = None,
           day_end: int | None = None, hour: int | None = None, player: str | None = None,
           game: int | None = None, only_protected: bool = False, limit: int = 300) -> list[dict]:
    sql = ["SELECT * FROM plays WHERE (protected_at IS NOT NULL OR start_ms > ?)"]
    args: list = [visible_since(cfg, now)]
    if day_start is not None and day_end is not None:
        sql.append("AND moment_ms >= ? AND moment_ms < ?")
        args += [day_start, day_end]
    if player:
        sql.append("AND turn_player = ?")
        args.append(player)
    if game is not None:
        sql.append("AND game_number = ?")
        args.append(game)
    if only_protected:
        sql.append("AND protected_at IS NOT NULL")
    sql.append("ORDER BY moment_ms DESC LIMIT ?")
    args.append(limit)
    rows = [dict(r) for r in conn.execute(" ".join(sql), args)]
    if hour is not None:
        rows = [r for r in rows if datetime.fromtimestamp(r["moment_ms"] / 1000).hour == hour]
    return [public(r, cfg) for r in rows]


def public(row, cfg: Config) -> dict:
    """La jugada como la ve la pantalla (sin la ruta del archivo)."""
    r = dict(row)
    r["protected"] = r["protected_at"] is not None
    if r["protected"]:
        r["expires_ms"] = r["protected_at"] + cfg.protected_days * DAY_MS if cfg.protected_days > 0 else None
    else:
        r["expires_ms"] = r["start_ms"] + cfg.retention_days * DAY_MS
    r["url"] = f"/jugada/{r['id']}.mp4" if r["protected"] else None
    r.pop("path", None)
    return r


def file_path(conn: sqlite3.Connection, cfg: Config, play_id: str) -> Path | None:
    row = get(conn, play_id)
    if row is None or not row["path"]:
        return None
    path = Path(row["path"])
    # Solo se sirven archivos de la carpeta de jugadas.
    if not path.resolve().is_relative_to(cfg.plays_dir.resolve()):
        return None
    return path


def options(conn: sqlite3.Connection, cfg: Config, now: int) -> dict:
    since = visible_since(cfg, now)
    where = "WHERE protected_at IS NOT NULL OR start_ms > ?"
    players = [r[0] for r in conn.execute(
        f"SELECT DISTINCT turn_player FROM plays {where} ORDER BY turn_player", (since,)) if r[0]]
    games = [{"number": r[0], "player1": r[1], "player2": r[2]} for r in conn.execute(
        f"""SELECT game_number, MAX(player1), MAX(player2) FROM plays {where}
            GROUP BY game_number ORDER BY game_number DESC""", (since,)) if r[0] is not None]
    days = sorted({datetime.fromtimestamp(r[0] / 1000).date().isoformat() for r in conn.execute(
        f"SELECT moment_ms FROM plays {where}", (since,))}, reverse=True)
    return {"players": players, "games": games, "days": days}


def expire(conn: sqlite3.Connection, cfg: Config, now: int) -> tuple[int, int]:
    """Borra las jugadas guardadas de más de `protected_days` días."""
    if cfg.protected_days <= 0:
        return 0, 0
    rows = conn.execute("SELECT id, path FROM plays WHERE protected_at IS NOT NULL AND protected_at < ?",
                        (now - cfg.protected_days * DAY_MS,)).fetchall()
    freed = 0
    for r in rows:
        path = Path(r["path"]) if r["path"] else None
        if path is not None and path.resolve().is_relative_to(cfg.plays_dir.resolve()):
            try:
                freed += path.stat().st_size
                path.unlink()
            except FileNotFoundError:
                pass
        conn.execute("DELETE FROM plays WHERE id = ?", (r["id"],))
    return len(rows), freed


def usage_bytes(cfg: Config) -> int:
    total = 0
    if cfg.plays_dir.exists():
        for p in cfg.plays_dir.rglob("*.mp4"):
            try:
                total += p.stat().st_size
            except FileNotFoundError:
                pass
    return total


def verify(conn: sqlite3.Connection) -> list[dict]:
    """Revisa que cada jugada protegida exista y no haya cambiado."""
    problems = []
    for r in conn.execute("SELECT id, path, sha256 FROM plays WHERE protected_at IS NOT NULL"):
        p = Path(r["path"])
        if not p.exists():
            problems.append({"id": r["id"], "path": r["path"], "problema": "falta el archivo"})
        elif sha256_file(p) != r["sha256"]:
            problems.append({"id": r["id"], "path": r["path"], "problema": "el archivo cambió"})
    return problems
