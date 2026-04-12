import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "vidloop_dash.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_DEFAULT) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                host_lan TEXT,
                port INTEGER NOT NULL DEFAULT 22,
                user TEXT NOT NULL,
                ssh_key_path TEXT,
                ssh_password TEXT,
                owner_user_id INTEGER,
                remote_path TEXT NOT NULL,
                remote_filename TEXT NOT NULL,
                restart_cmd TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(devices)").fetchall()
        }
        if "ssh_password" not in columns:
            conn.execute("ALTER TABLE devices ADD COLUMN ssh_password TEXT")
        if "owner_user_id" not in columns:
            conn.execute("ALTER TABLE devices ADD COLUMN owner_user_id INTEGER")
        if "host_lan" not in columns:
            conn.execute("ALTER TABLE devices ADD COLUMN host_lan TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                video_path TEXT NOT NULL,
                detail TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS video_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source_type TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                filename TEXT,
                batch_id TEXT,
                upload_id INTEGER,
                device_id INTEGER,
                device_name TEXT,
                detail TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                content_type TEXT NOT NULL,
                content_reference TEXT,
                time_start TEXT NOT NULL,
                time_end TEXT NOT NULL,
                days_of_week TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            )
            """
        )


def get_devices(owner_user_id: Optional[int] = None, db_path: Path = DB_DEFAULT) -> List[Dict[str, Any]]:
    with _connect(db_path) as conn:
        if owner_user_id is None:
            rows = conn.execute(
                """
                SELECT d.*, u.username AS owner_username
                FROM devices d
                LEFT JOIN users u ON u.id = d.owner_user_id
                WHERE d.enabled = 1
                ORDER BY d.created_at DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT d.*, u.username AS owner_username
                FROM devices d
                LEFT JOIN users u ON u.id = d.owner_user_id
                WHERE d.enabled = 1 AND d.owner_user_id = ?
                ORDER BY d.created_at DESC
                """,
                (owner_user_id,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_users(db_path: Path = DB_DEFAULT) -> List[Dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT id, username, role, created_at FROM users").fetchall()
    return [dict(row) for row in rows]


def get_user_by_username(username: str, db_path: Path = DB_DEFAULT) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        return None
    return dict(row)


def add_user(username: str, password_hash: str, role: str, db_path: Path = DB_DEFAULT) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, password_hash, role),
        )

def update_user(user_id: int, username: str, role: str, password_hash: Optional[str] = None, db_path: Path = DB_DEFAULT) -> None:
    with _connect(db_path) as conn:
        if password_hash:
            conn.execute(
                "UPDATE users SET username = ?, role = ?, password_hash = ? WHERE id = ?",
                (username, role, password_hash, user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET username = ?, role = ? WHERE id = ?",
                (username, role, user_id),
            )

def delete_user(user_id: int, db_path: Path = DB_DEFAULT) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def count_users(db_path: Path = DB_DEFAULT) -> int:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(1) AS total FROM users").fetchone()
    return int(row["total"])


def add_device(data: Dict[str, Any], db_path: Path = DB_DEFAULT) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO devices
            (name, host, host_lan, port, user, ssh_key_path, ssh_password, owner_user_id, remote_path, remote_filename, restart_cmd, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                data["name"],
                data["host"],
                data.get("host_lan"),
                data["port"],
                data["user"],
                data.get("ssh_key_path"),
                data.get("ssh_password"),
                data.get("owner_user_id"),
                data["remote_path"],
                data["remote_filename"],
                data.get("restart_cmd"),
            ),
        )


def delete_device(device_id: int, db_path: Path = DB_DEFAULT) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))


def update_device(device_id: int, data: Dict[str, Any], db_path: Path = DB_DEFAULT) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE devices
            SET name = ?,
                host = ?,
                host_lan = ?,
                port = ?,
                user = ?,
                ssh_key_path = ?,
                ssh_password = ?,
                owner_user_id = ?,
                remote_path = ?,
                remote_filename = ?,
                restart_cmd = ?
            WHERE id = ?
            """,
            (
                data["name"],
                data["host"],
                data.get("host_lan"),
                data["port"],
                data["user"],
                data.get("ssh_key_path"),
                data.get("ssh_password"),
                data.get("owner_user_id"),
                data["remote_path"],
                data["remote_filename"],
                data.get("restart_cmd"),
                device_id,
            ),
        )


def create_upload(
    batch_id: str, status: str, video_path: str, detail: Optional[str], db_path: Path = DB_DEFAULT
) -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO uploads (batch_id, status, video_path, detail) VALUES (?, ?, ?, ?)",
            (batch_id, status, video_path, detail),
        )
        return int(cur.lastrowid)


def update_upload_status(upload_id: int, status: str, detail: Optional[str], db_path: Path = DB_DEFAULT) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE uploads SET status = ?, detail = ? WHERE id = ?",
            (status, detail, upload_id),
        )


def get_uploads(db_path: Path = DB_DEFAULT) -> List[Dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM uploads ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def get_upload(upload_id: int, db_path: Path = DB_DEFAULT) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def delete_upload(upload_id: int, db_path: Path = DB_DEFAULT) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))


def delete_video_history_by_upload(upload_id: int, db_path: Path = DB_DEFAULT) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM video_history WHERE upload_id = ?", (upload_id,))


def create_video_history(
    source_type: str,
    action: str,
    status: str,
    filename: Optional[str] = None,
    batch_id: Optional[str] = None,
    upload_id: Optional[int] = None,
    device_id: Optional[int] = None,
    device_name: Optional[str] = None,
    detail: Optional[str] = None,
    db_path: Path = DB_DEFAULT,
) -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO video_history
            (source_type, action, status, filename, batch_id, upload_id, device_id, device_name, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_type, action, status, filename, batch_id, upload_id, device_id, device_name, detail),
        )
        return int(cur.lastrowid)


def get_video_history(
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    db_path: Path = DB_DEFAULT,
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM video_history"
    where_clauses: List[str] = []
    params: List[Any] = []

    if source_type:
        where_clauses.append("source_type = ?")
        params.append(source_type)
    if status:
        where_clauses.append("status = ?")
        params.append(status)

    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    query += " ORDER BY created_at DESC, id DESC"

    with _connect(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def add_schedule(data: Dict[str, Any], db_path: Path = DB_DEFAULT) -> int:
    """Crea una nueva programación"""
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO schedules
            (device_id, content_type, content_reference, time_start, time_end, days_of_week, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["device_id"],
                data["content_type"],
                data.get("content_reference", ""),
                data["time_start"],
                data["time_end"],
                data["days_of_week"],
                data.get("enabled", 1),
            ),
        )
        return int(cur.lastrowid)


def get_schedules(device_id: Optional[int] = None, db_path: Path = DB_DEFAULT) -> List[Dict[str, Any]]:
    """Obtiene todas las programaciones, opcionalmente filtradas por dispositivo"""
    with _connect(db_path) as conn:
        if device_id is None:
            rows = conn.execute(
                """
                SELECT s.*, d.name as device_name
                FROM schedules s
                LEFT JOIN devices d ON d.id = s.device_id
                ORDER BY s.time_start ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT s.*, d.name as device_name
                FROM schedules s
                LEFT JOIN devices d ON d.id = s.device_id
                WHERE s.device_id = ?
                ORDER BY s.time_start ASC
                """,
                (device_id,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_schedule(schedule_id: int, db_path: Path = DB_DEFAULT) -> Optional[Dict[str, Any]]:
    """Obtiene una programación específica por ID"""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT s.*, d.name as device_name
            FROM schedules s
            LEFT JOIN devices d ON d.id = s.device_id
            WHERE s.id = ?
            """,
            (schedule_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def update_schedule(schedule_id: int, data: Dict[str, Any], db_path: Path = DB_DEFAULT) -> None:
    """Actualiza una programación existente"""
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE schedules
            SET device_id = ?,
                content_type = ?,
                content_reference = ?,
                time_start = ?,
                time_end = ?,
                days_of_week = ?,
                enabled = ?
            WHERE id = ?
            """,
            (
                data["device_id"],
                data["content_type"],
                data.get("content_reference", ""),
                data["time_start"],
                data["time_end"],
                data["days_of_week"],
                data.get("enabled", 1),
                schedule_id,
            ),
        )


def delete_schedule(schedule_id: int, db_path: Path = DB_DEFAULT) -> None:
    """Elimina una programación"""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))


def toggle_schedule(schedule_id: int, enabled: bool, db_path: Path = DB_DEFAULT) -> None:
    """Activa o desactiva una programación"""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE schedules SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, schedule_id),
        )
