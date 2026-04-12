#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def load_mapping(path: Path) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        mapping: dict[str, dict[str, Any]] = {}
        for name, value in raw.items():
            if isinstance(value, str):
                mapping[str(name)] = {"host": value}
            elif isinstance(value, dict):
                mapping[str(name)] = value
        return mapping

    if isinstance(raw, list):
        mapping = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            host = str(item.get("host") or "").strip()
            if not name or not host:
                continue
            mapping[name] = item
        return mapping

    raise ValueError("El mapping JSON debe ser un objeto o lista de objetos")


def main() -> int:
    parser = argparse.ArgumentParser(description="Actualiza dispositivos del dashboard para usar IPs VPN")
    parser.add_argument("--db", default="data/vidloop_dash.db", help="Ruta al SQLite del dashboard")
    parser.add_argument("--mapping-file", required=True, help="JSON con nombre => host o lista de objetos")
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra cambios, no escribe")
    args = parser.parse_args()

    db_path = Path(args.db)
    mapping_path = Path(args.mapping_file)

    if not db_path.exists():
        raise SystemExit(f"DB no encontrada: {db_path}")
    if not mapping_path.exists():
        raise SystemExit(f"Mapping no encontrado: {mapping_path}")

    mapping = load_mapping(mapping_path)
    if not mapping:
        raise SystemExit("No hay entradas válidas en el mapping")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, name, host, port, user FROM devices WHERE enabled = 1 ORDER BY id").fetchall()

    updated = 0
    for row in rows:
        name = str(row["name"])
        if name not in mapping:
            continue

        target = mapping[name]
        new_host = str(target.get("host") or row["host"]).strip()
        new_port = int(target.get("port") or row["port"])
        new_user = str(target.get("user") or row["user"]).strip()

        changed = []
        if new_host != row["host"]:
            changed.append(f"host: {row['host']} -> {new_host}")
        if new_port != row["port"]:
            changed.append(f"port: {row['port']} -> {new_port}")
        if new_user != row["user"]:
            changed.append(f"user: {row['user']} -> {new_user}")

        if not changed:
            continue

        print(f"[{row['id']}] {name}: " + "; ".join(changed))
        if not args.dry_run:
            conn.execute(
                "UPDATE devices SET host = ?, port = ?, user = ? WHERE id = ?",
                (new_host, new_port, new_user, int(row["id"])),
            )
        updated += 1

    if args.dry_run:
        print(f"Dry run completo. Cambios detectados: {updated}")
    else:
        conn.commit()
        print(f"Actualización completa. Dispositivos modificados: {updated}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
