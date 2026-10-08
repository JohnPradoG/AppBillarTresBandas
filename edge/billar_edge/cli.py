"""Comando `billar`: punto de entrada de los servicios y herramientas de diagnóstico."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from . import config as config_mod
from . import events, health, live, recorder, retention, statefile, web
from .db import connect


def _ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%d/%m/%Y %H:%M:%S")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="billar", description="Sistema de grabación de la mesa de billar")
    parser.add_argument("--config", help=f"archivo de configuración (por defecto {config_mod.DEFAULT_CONFIG_PATH})")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("record", help="graba una cámara de forma continua (servicio)")
    p.add_argument("camera_id")
    p = sub.add_parser("live", help="señal en vivo de una cámara para la pantalla (servicio)")
    p.add_argument("camera_id")
    sub.add_parser("ui", help="servidor local de la pantalla táctil (servicio)")
    sub.add_parser("health", help="monitor de salud (servicio)")
    sub.add_parser("retention", help="borra los segmentos vencidos (lo ejecuta un temporizador)")
    sub.add_parser("status", help="muestra el estado actual")
    p = sub.add_parser("events", help="muestra el registro de eventos")
    p.add_argument("-n", type=int, default=30)
    sub.add_parser("check-config", help="valida el archivo de configuración")
    sub.add_parser("cameras", help="lista los ids de las cámaras configuradas")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    cfg = config_mod.load(args.config)

    if args.command == "record":
        recorder.main(cfg, args.camera_id)
    elif args.command == "live":
        live.main(cfg, args.camera_id)
    elif args.command == "ui":
        web.main(cfg)
    elif args.command == "health":
        health.main(cfg)
    elif args.command == "retention":
        conn = connect(cfg.db_path)
        result = retention.run(conn, cfg)
        print(f"Borrados por antigüedad: {result.deleted_expired}; por espacio: {result.deleted_for_space}; "
              f"liberados {result.freed_bytes / 1e9:.2f} GB")
    elif args.command == "status":
        report = statefile.read(cfg.run_dir / "status.json")
        if report is None:
            print("Sin estado: el monitor de salud no está funcionando.")
            return 1
        print(f"Estado: {report['label']}")
        for cam in report["cameras"]:
            extra = f" ({cam['detail']})" if cam.get("detail") else ""
            print(f"  Mesa {cam['table_number']} · {cam['camera_id']}: {cam['label']}{extra}")
        st = report["storage"]
        print(f"  Disco: {st['state']}, {st['free_percent']} % libre" if st["free_percent"] is not None
              else f"  Disco: {st['detail']}")
        return 0 if report["ok"] else 2
    elif args.command == "events":
        conn = connect(cfg.db_path)
        for row in reversed(events.recent(conn, args.n)):
            cam = f" [{row['camera_id']}]" if row["camera_id"] else ""
            print(f"{_ts(row['ts'])}  {row['level']:<11}{cam} {row['message']}")
    elif args.command == "cameras":
        for cam in cfg.cameras:
            print(cam.id)
    elif args.command == "check-config":
        print(f"Configuración válida: {len(cfg.cameras)} cámara(s), retención {cfg.retention_days} días, "
              f"segmentos de {cfg.segment_seconds} s, video en {cfg.recordings_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
