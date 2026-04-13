from __future__ import annotations
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app import db
from app.ssh_utils import run_scp, run_scp_download, run_ssh_command

ROOT_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers de conexión a dispositivos — usan fallback VPN→LAN automáticamente
# ---------------------------------------------------------------------------

def _ssh(dev: dict, command: str, timeout_sec: int = 12) -> str:
    """Ejecuta un comando SSH en un device dict con fallback LAN y retry."""
    return run_ssh_command(
        user=dev["user"],
        host=dev["host"],
        port=dev.get("port", 22),
        ssh_key_path=dev.get("ssh_key_path"),
        ssh_password=dev.get("ssh_password"),
        command=command,
        timeout_sec=timeout_sec,
        host_fallback=dev.get("host_lan"),
    )


def _scp(local_path: str, dev: dict, remote_path: str, timeout_sec: int = 20) -> None:
    """SCP atómico con fallback LAN y retry (no deja archivos corruptos)."""
    run_scp(
        local_path=local_path,
        user=dev["user"],
        host=dev["host"],
        port=dev.get("port", 22),
        ssh_key_path=dev.get("ssh_key_path"),
        ssh_password=dev.get("ssh_password"),
        remote_path=remote_path,
        timeout_sec=timeout_sec,
        host_fallback=dev.get("host_lan"),
    )


def _scp_dn(dev: dict, remote_path: str, local_path: str, timeout_sec: int = 20) -> None:
    """SCP download con fallback LAN y retry."""
    run_scp_download(
        user=dev["user"],
        host=dev["host"],
        port=dev.get("port", 22),
        ssh_key_path=dev.get("ssh_key_path"),
        ssh_password=dev.get("ssh_password"),
        remote_path=remote_path,
        local_path=local_path,
        timeout_sec=timeout_sec,
        host_fallback=dev.get("host_lan"),
    )


# ---------------------------------------------------------------------------
UPLOADS_DIR = ROOT_DIR / "uploads"
DEFAULT_REMOTE_PATH = "/home/admin/videos"
DEFAULT_RESTART_CMD = "sudo systemctl restart video_looper"
DEFAULT_DEVICE_LOG_PATH = "/var/log/vidloop44.log"
RADIO_ENV_FILE = "/etc/default/vidloop-radio"
RADIO_SERVICE_NAME = "vidloop-radio"
NORMALIZER_SERVICE_NAME = "vidloop-media-normalizer.service"
NORMALIZER_TIMER_NAME = "vidloop-media-normalizer.timer"
ENV_FILE = ROOT_DIR / ".env"
MAINTENANCE_FILE = ROOT_DIR / "data" / "maintenance_config.json"
load_dotenv(ENV_FILE)


def _static_default_devices() -> List[Dict[str, Any]]:
    default_password = os.environ.get("DEFAULT_DEVICE_PASSWORD")
    return [
        {
            "name": "Principal",
            "host": "192.168.192.109",
            "port": 22,
            "user": "admin",
            "ssh_key_path": None,
            "ssh_password": default_password,
            "remote_path": "/home/admin/videos",
            "remote_filename": "content.mp4",
            "restart_cmd": DEFAULT_RESTART_CMD,
        },
        {
            "name": "Entrada",
            "host": "192.168.192.227",
            "port": 22,
            "user": "admin",
            "ssh_key_path": None,
            "ssh_password": default_password,
            "remote_path": "/home/admin/videos",
            "remote_filename": "content.mp4",
            "restart_cmd": DEFAULT_RESTART_CMD,
        },
        {
            "name": "Fondo",
            "host": "192.168.192.33",
            "port": 22,
            "user": "admin",
            "ssh_key_path": None,
            "ssh_password": default_password,
            "remote_path": "/home/admin/videos",
            "remote_filename": "content.mp4",
            "restart_cmd": DEFAULT_RESTART_CMD,
        },
    ]


def _load_default_devices_from_env() -> List[Dict[str, Any]]:
    raw = (os.environ.get("DEFAULT_DEVICES_JSON") or "").strip()
    if not raw:
        return _static_default_devices()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _static_default_devices()

    if not isinstance(parsed, list):
        return _static_default_devices()

    default_password = os.environ.get("DEFAULT_DEVICE_PASSWORD")
    devices: List[Dict[str, Any]] = []
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"Dispositivo {index}").strip()
        host = str(item.get("host") or "").strip()
        user = str(item.get("user") or "admin").strip()
        remote_path = str(item.get("remote_path") or DEFAULT_REMOTE_PATH).strip()
        remote_filename = str(item.get("remote_filename") or "content.mp4").strip()
        if not name or not host or not user:
            continue
        devices.append(
            {
                "name": name,
                "host": host,
                "port": int(item.get("port") or 22),
                "user": user,
                "ssh_key_path": item.get("ssh_key_path"),
                "ssh_password": item.get("ssh_password", default_password),
                "remote_path": remote_path,
                "remote_filename": remote_filename,
                "restart_cmd": str(item.get("restart_cmd") or DEFAULT_RESTART_CMD).strip(),
            }
        )

    return devices or _static_default_devices()


DEFAULT_DEVICES = _load_default_devices_from_env()
SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(32)
SESSION_MAX_AGE = max(1800, int(os.environ.get("SESSION_MAX_AGE", "43200")))
SESSION_COOKIE_NAME = (os.environ.get("SESSION_COOKIE_NAME") or "vidloop_session").strip() or "vidloop_session"
SESSION_COOKIE_SECURE = (os.environ.get("SESSION_COOKIE_SECURE") or "").strip().lower() in {"1", "true", "yes", "on"}
PUBLIC_PATHS = {"/login", "/logout"}
PUBLIC_PREFIXES = ("/static",)


class AuthRequiredMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        user = _get_session_user(request)
        if not user:
            if path.startswith("/api/"):
                return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"ok": False, "error": "Sesion expirada"})
            return RedirectResponse(
                url=_build_login_url(next_target=_requested_path(request), err="Iniciá sesión para continuar"),
                status_code=303,
            )

        request.state.current_user = user
        return await call_next(request)


app = FastAPI(title="VIDLOOP-DASHBOARD", version="1.0.0")
app.add_middleware(AuthRequiredMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie=SESSION_COOKIE_NAME,
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=SESSION_COOKIE_SECURE,
)
app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT_DIR / "templates"))
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _ensure_dirs() -> None:
    (ROOT_DIR / "data").mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def _load_maintenance_config() -> Dict[str, Any]:
    default_config = {"enabled": False, "message": ""}
    if not MAINTENANCE_FILE.exists():
        return default_config

    try:
        raw = json.loads(MAINTENANCE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_config

    enabled = bool(raw.get("enabled", False))
    message = str(raw.get("message", "")).strip()
    return {"enabled": enabled, "message": message}


def _save_maintenance_config(enabled: bool, message: str) -> Dict[str, Any]:
    payload = {"enabled": bool(enabled), "message": (message or "").strip()}
    MAINTENANCE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


@app.on_event("startup")
def on_startup() -> None:
    _ensure_dirs()
    db.init_db()
    _ensure_admin_user()
    _ensure_default_devices()


def _ensure_admin_user() -> None:
    if db.count_users() > 0:
        return

    admin_user = (os.environ.get("ADMIN_USER") or "adminvidloop").strip().lower() or "adminvidloop"
    admin_pass = (os.environ.get("ADMIN_PASS") or "vidloop4455").strip() or "vidloop4455"
    password_hash = pwd_context.hash(admin_pass)
    db.add_user(admin_user, password_hash, "admin")


def _ensure_default_devices() -> None:
    if db.get_devices():
        return

    for device in DEFAULT_DEVICES:
        db.add_device(device)


def _requested_path(request: Request) -> str:
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{request.url.path}{query}"


def _is_safe_next_target(target: Optional[str]) -> bool:
    if not target:
        return False
    return target.startswith("/") and not target.startswith("//")


def _build_login_url(
    next_target: Optional[str] = None,
    err: Optional[str] = None,
    msg: Optional[str] = None,
) -> str:
    params: Dict[str, str] = {}
    if _is_safe_next_target(next_target):
        params["next"] = str(next_target)
    if err:
        params["err"] = str(err)
    if msg:
        params["msg"] = str(msg)
    if not params:
        return "/login"
    return f"/login?{urlencode(params)}"


def _get_session_user(request: Request) -> Optional[dict]:
    raw_user_id = request.session.get("user_id")
    if raw_user_id is None:
        return None

    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        request.session.clear()
        return None

    user = db.get_user_by_id(user_id)
    if not user:
        request.session.clear()
        return None
    return user


def _store_session_user(request: Request, user: dict) -> None:
    request.session.clear()
    request.session["user_id"] = int(user["id"])


def _get_current_user(request: Request) -> dict:
    user = getattr(request.state, "current_user", None)
    if user is not None:
        return user

    user = _get_session_user(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesion no iniciada")
    request.state.current_user = user
    return user


def _require_admin(user: dict = Depends(_get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso denegado")
    return user


def _build_video_from_images(image_paths: List[Path], duration_sec: int, output_path: Path) -> None:
    work_dir = output_path.parent
    list_file = work_dir / "list.txt"
    with list_file.open("w", encoding="utf-8") as handle:
        for image_path in image_paths:
            handle.write(f"file '{image_path.as_posix()}'\n")
            handle.write(f"duration {duration_sec}\n")
        handle.write(f"file '{image_paths[-1].as_posix()}'\n")

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-vf",
        "scale=1920:1080,setsar=1",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        "-r",
        "25",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    result = shutil.which("ffmpeg")
    if not result:
        raise RuntimeError("FFmpeg no esta instalado o no esta en PATH")
    import subprocess

    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _save_images(batch_dir: Path, files: List[UploadFile]) -> List[Path]:
    images_dir = batch_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: List[Path] = []
    for index, upload in enumerate(files, start=1):
        if not upload.filename:
            continue
        extension = Path(upload.filename).suffix.lower()
        if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        target = images_dir / f"image_{index:03d}{extension}"
        with target.open("wb") as handle:
            handle.write(upload.file.read())
        saved_paths.append(target)
    return saved_paths


def _visible_devices_for_user(user: dict) -> List[dict]:
    if user["role"] == "admin":
        return db.get_devices()
    return db.get_devices(owner_user_id=int(user["id"]))


def _inventory_rows(devices: List[dict]) -> List[dict]:
    rows: List[dict] = []
    for device in sorted(devices, key=lambda item: ((item.get("name") or "").lower(), int(item.get("id") or 0))):
        wireguard_ip = str(device.get("host") or "").strip()
        local_ip = str(device.get("host_lan") or "").strip()
        rows.append(
            {
                "id": int(device.get("id") or 0),
                "name": str(device.get("name") or "Sin nombre").strip() or "Sin nombre",
                "wireguard_ip": wireguard_ip,
                "local_ip": local_ip,
                "port": int(device.get("port") or 22),
                "owner_username": str(device.get("owner_username") or "").strip(),
                "routing_mode": "WireGuard + LAN fallback" if local_ip else "Solo WireGuard",
            }
        )
    return rows


@app.get("/login")
def login_page(request: Request):
    if _get_session_user(request):
        return RedirectResponse(url="/", status_code=303)

    next_target = request.query_params.get("next") or "/"
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "err": request.query_params.get("err"),
            "msg": request.query_params.get("msg"),
            "next": next_target if _is_safe_next_target(next_target) else "/",
            "prefilled_username": request.query_params.get("username", ""),
        },
    )


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    clean_username = username.strip()
    user = db.get_user_by_username(clean_username)
    if user is None and clean_username.lower() != clean_username:
        user = db.get_user_by_username(clean_username.lower())

    if not user or not pwd_context.verify(password, user["password_hash"]):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "err": "Usuario o contraseña inválidos",
                "msg": None,
                "next": next if _is_safe_next_target(next) else "/",
                "prefilled_username": clean_username,
            },
            status_code=400,
        )

    _store_session_user(request, user)
    return RedirectResponse(url=next if _is_safe_next_target(next) else "/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url=_build_login_url(msg="Sesión cerrada"), status_code=303)


@app.get("/")
def index(request: Request, user: dict = Depends(_get_current_user)):
    devices = _visible_devices_for_user(user)
    uploads = db.get_uploads()
    users = db.get_users() if user["role"] == "admin" else []
    schedules = db.get_schedules()
    msg = request.query_params.get("msg")
    err = request.query_params.get("err")
    maintenance_config = _load_maintenance_config()
    maintenance_message = maintenance_config["message"]
    maintenance_enabled = maintenance_config["enabled"]

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "devices": devices,
            "uploads": uploads,
            "users": users,
            "schedules": schedules,
            "current_user": user,
            "is_admin": user["role"] == "admin",
            "can_manage_schedules": user["role"] in ["admin", "owner"],
            "msg": msg,
            "err": err,
            "maintenance_message": maintenance_message,
            "maintenance_enabled": maintenance_enabled,
        },
    )


@app.get("/admin/ip-dashboard")
def ip_dashboard(request: Request, user: dict = Depends(_get_current_user)):
    if user["role"] != "admin":
        return RedirectResponse(url="/?err=Acceso denegado", status_code=303)

    devices = db.get_devices()
    rows = _inventory_rows(devices)
    maintenance_config = _load_maintenance_config()

    return templates.TemplateResponse(
        "ip_dashboard.html",
        {
            "request": request,
            "current_user": user,
            "is_admin": True,
            "maintenance_message": maintenance_config["message"],
            "maintenance_enabled": maintenance_config["enabled"],
            "inventory_rows": rows,
            "inventory_summary": {
                "total": len(rows),
                "with_wireguard": sum(1 for row in rows if row["wireguard_ip"]),
                "with_local": sum(1 for row in rows if row["local_ip"]),
                "without_local": sum(1 for row in rows if not row["local_ip"]),
            },
        },
    )


@app.post("/admin/maintenance")
def save_maintenance_settings(
    payload: Dict[str, Any] = Body(...),
    _: dict = Depends(_require_admin),
):
    enabled = bool(payload.get("enabled", False))
    message = str(payload.get("message", ""))

    saved = _save_maintenance_config(enabled=enabled, message=message)
    return {"ok": True, "enabled": saved["enabled"], "message": saved["message"]}


@app.get("/admin/maintenance")
def get_maintenance_settings(_: dict = Depends(_get_current_user)):
    config = _load_maintenance_config()
    return {"ok": True, "enabled": config["enabled"], "message": config["message"]}


@app.post("/devices/add")
def add_device(
    _: dict = Depends(_require_admin),
    name: str = Form(...),
    host: str = Form(...),
    host_lan: Optional[str] = Form(None),
    port: int = Form(22),
    user: str = Form(...),
    ssh_password: Optional[str] = Form(None),
    owner_user_id: Optional[str] = Form(None),
    remote_path: str = Form(DEFAULT_REMOTE_PATH),
    remote_filename: str = Form("content.mp4"),
    restart_cmd: Optional[str] = Form(DEFAULT_RESTART_CMD),
):
    db.add_device(
        {
            "name": name.strip(),
            "host": host.strip(),
            "host_lan": host_lan.strip() if host_lan and host_lan.strip() else None,
            "port": int(port),
            "user": user.strip(),
            "ssh_key_path": None,
            "ssh_password": ssh_password.strip() if ssh_password else None,
            "owner_user_id": int(owner_user_id) if owner_user_id and owner_user_id.strip() else None,
            "remote_path": (remote_path or DEFAULT_REMOTE_PATH).strip(),
            "remote_filename": remote_filename.strip(),
            "restart_cmd": (restart_cmd or DEFAULT_RESTART_CMD).strip(),
        }
    )
    return RedirectResponse(url="/?msg=Dispositivo agregado", status_code=303)


@app.get("/uploads/{upload_id}")
def preview_upload(upload_id: int, _: dict = Depends(_get_current_user)):
    upload = db.get_upload(upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload no encontrado")

    video_path = Path(upload["video_path"]).resolve()
    try:
        video_path.relative_to(UPLOADS_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Ruta invalida") from exc

    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video no encontrado")

    return FileResponse(video_path, media_type="video/mp4")


@app.post("/uploads/{upload_id}/delete")
def delete_generated_upload(upload_id: int, _: dict = Depends(_get_current_user)):
    upload = db.get_upload(upload_id)
    if not upload:
        return RedirectResponse(url="/?err=Video no encontrado", status_code=303)

    video_path = Path(upload["video_path"]).resolve()
    try:
        video_path.relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        return RedirectResponse(url="/?err=Ruta de video invalida", status_code=303)

    try:
        if video_path.exists() and video_path.is_file():
            video_path.unlink()

        parent_dir = video_path.parent
        if parent_dir != UPLOADS_DIR and parent_dir.exists():
            try:
                parent_dir.rmdir()
            except OSError:
                pass

        db.delete_video_history_by_upload(upload_id)
        db.delete_upload(upload_id)
    except Exception as exc:
        return RedirectResponse(url=f"/?err=No se pudo eliminar video: {exc}", status_code=303)

    return RedirectResponse(url="/?msg=Video generado eliminado", status_code=303)


@app.post("/devices/{device_id}/delete")
def remove_device(device_id: int, _: dict = Depends(_require_admin)):
    db.delete_device(device_id)
    return RedirectResponse(url="/?msg=Dispositivo eliminado", status_code=303)


@app.post("/devices/{device_id}/update")
def update_device(
    device_id: int,
    _: dict = Depends(_require_admin),
    name: str = Form(...),
    host: str = Form(...),
    host_lan: Optional[str] = Form(None),
    port: int = Form(22),
    user: str = Form(...),
    ssh_password: Optional[str] = Form(None),
    owner_user_id: Optional[str] = Form(None),
    remote_path: str = Form(DEFAULT_REMOTE_PATH),
    remote_filename: str = Form("content.mp4"),
    restart_cmd: Optional[str] = Form(None),
):
    devices = {device["id"]: device for device in db.get_devices()}
    if device_id not in devices:
        return RedirectResponse(url="/?err=Dispositivo no encontrado", status_code=303)

    db.update_device(
        device_id,
        {
            "name": name.strip(),
            "host": host.strip(),
            "host_lan": host_lan.strip() if host_lan and host_lan.strip() else None,
            "port": int(port),
            "user": user.strip(),
            "ssh_key_path": None,
            "ssh_password": (
                ssh_password.strip()
                if ssh_password and ssh_password.strip()
                else devices[device_id].get("ssh_password")
            ),
            "owner_user_id": int(owner_user_id) if owner_user_id and owner_user_id.strip() else None,
            "remote_path": (remote_path or DEFAULT_REMOTE_PATH).strip(),
            "remote_filename": remote_filename.strip(),
            "restart_cmd": restart_cmd.strip() if restart_cmd else None,
        },
    )
    return RedirectResponse(url="/?msg=Dispositivo actualizado", status_code=303)



@app.post("/users/add")
def add_user(
    _: dict = Depends(_require_admin),
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("operator"),
):
    clean_username = username.strip().lower()
    clean_role = role.strip().lower()
    if clean_role not in {"admin", "operator"}:
        return RedirectResponse(url="/?err=Rol invalido", status_code=303)

    if db.get_user_by_username(clean_username):
        return RedirectResponse(url="/?err=Usuario ya existe", status_code=303)

    if len(password) < 6:
        return RedirectResponse(url="/?err=Password muy corta", status_code=303)

    password_hash = pwd_context.hash(password)
    db.add_user(clean_username, password_hash, clean_role)
    return RedirectResponse(url="/?msg=Usuario agregado", status_code=303)

# --- Edición y borrado de usuarios (solo admin) ---
@app.post("/users/{user_id}/edit")
def edit_user(
    user_id: int,
    _: dict = Depends(_require_admin),
    username: str = Form(...),
    role: str = Form(...),
    password: str = Form("")
):
    clean_username = username.strip().lower()
    clean_role = role.strip().lower()
    if clean_role not in {"admin", "operator"}:
        return RedirectResponse(url="/?err=Rol invalido", status_code=303)

    existing = db.get_user_by_username(clean_username)
    if existing and int(existing["id"]) != user_id:
        return RedirectResponse(url="/?err=Usuario ya existe", status_code=303)

    password_hash = None
    if password and len(password) >= 6:
        password_hash = pwd_context.hash(password)
    elif password:
        return RedirectResponse(url="/?err=Password muy corta", status_code=303)

    db.update_user(user_id, clean_username, clean_role, password_hash)
    return RedirectResponse(url="/?msg=Usuario actualizado", status_code=303)

@app.post("/users/{user_id}/delete")
def delete_user(user_id: int, _: dict = Depends(_require_admin)):
    db.delete_user(user_id)
    return RedirectResponse(url="/?msg=Usuario eliminado", status_code=303)


@app.post("/upload-images")
def upload_images(
    _: dict = Depends(_get_current_user),
    duration_sec: int = Form(20),
    images: List[UploadFile] = File(...),
):
    batch_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    batch_dir = UPLOADS_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    saved = _save_images(batch_dir, images)
    if not saved:
        return RedirectResponse(url="/?err=No se subieron imagenes validas", status_code=303)

    video_path = batch_dir / "VidLoop.mp4"
    upload_id = db.create_upload(batch_id, "processing", str(video_path), None)

    try:
        _build_video_from_images(saved, max(1, duration_sec), video_path)
    except Exception as exc:
        db.update_upload_status(upload_id, "error", str(exc))
        db.create_video_history(
            source_type="imagenes",
            action="generar",
            status="error",
            batch_id=batch_id,
            upload_id=upload_id,
            filename=video_path.name,
            detail=str(exc),
        )
        return RedirectResponse(url=f"/?err=Error al generar video: {exc}", status_code=303)

    db.update_upload_status(upload_id, "ready", None)
    db.create_video_history(
        source_type="imagenes",
        action="generar",
        status="ok",
        batch_id=batch_id,
        upload_id=upload_id,
        filename=video_path.name,
        detail="Video generado desde imágenes",
    )
    return FileResponse(video_path, media_type="video/mp4", filename=video_path.name)


@app.post("/deploy/{upload_id}")
def deploy_video(
    upload_id: int,
    device_ids: List[int] = Form(...),
    user: dict = Depends(_get_current_user),
):
    upload = db.get_upload(upload_id)
    if not upload:
        return RedirectResponse(url="/?err=Upload no encontrado", status_code=303)

    video_path = Path(upload["video_path"])
    if not video_path.exists():
        return RedirectResponse(url="/?err=Video no encontrado", status_code=303)

    devices = {device["id"]: device for device in _visible_devices_for_user(user)}
    errors: List[str] = []

    for device_id in device_ids:
        device = devices.get(int(device_id))
        if not device:
            continue
        remote_dir = device["remote_path"].rstrip("/")
        remote_file = f"{remote_dir}/{device['remote_filename']}"
        try:
            _ssh(device, f"mkdir -p '{remote_dir}'",
            )
            _scp(str(video_path), device, remote_file,
            )
            if device.get("restart_cmd"):
                _ssh(device, _restart_video_service_command(device),
                )
        except Exception as exc:
            errors.append(f"{device['name']}: {exc}")

    if errors:
        db.create_video_history(
            source_type="imagenes",
            action="envio_generado",
            status="error",
            batch_id=upload.get("batch_id"),
            upload_id=int(upload["id"]),
            filename=Path(upload["video_path"]).name,
            detail="; ".join(errors),
        )
        return RedirectResponse(
            url="/?err=" + "; ".join(errors),
            status_code=303,
        )

    db.create_video_history(
        source_type="imagenes",
        action="envio_generado",
        status="ok",
        batch_id=upload.get("batch_id"),
        upload_id=int(upload["id"]),
        filename=Path(upload["video_path"]).name,
        detail=f"Enviado a {len([d for d in device_ids if int(d) in devices])} dispositivo(s)",
    )
    return RedirectResponse(url="/?msg=Video desplegado", status_code=303)


@app.post("/devices/deploy-upload")
def deploy_uploaded_video(
    video: UploadFile = File(...),
    device_ids: List[int] = Form(...),
    user: dict = Depends(_get_current_user),
):
    if not video.filename:
        return RedirectResponse(url="/?err=Archivo invalido", status_code=303)

    safe_name = _safe_remote_filename(video.filename)
    if not _allowed_video_extension(safe_name):
        return RedirectResponse(url="/?err=Formato no permitido", status_code=303)

    visible_devices = {device["id"]: device for device in _visible_devices_for_user(user)}
    selected_devices = [visible_devices.get(int(device_id)) for device_id in device_ids]
    selected_devices = [device for device in selected_devices if device is not None]
    if not selected_devices:
        return RedirectResponse(url="/?err=No hay dispositivos validos seleccionados", status_code=303)

    temp_dir = UPLOADS_DIR / "_bulk_uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / f"{uuid.uuid4().hex}_{safe_name}"

    errors: List[str] = []
    try:
        with temp_file.open("wb") as handle:
            shutil.copyfileobj(video.file, handle)

        for device in selected_devices:
            remote_dir = (device.get("remote_path") or DEFAULT_REMOTE_PATH).rstrip("/")
            remote_file = f"{remote_dir}/{device.get('remote_filename') or safe_name}"
            try:
                _ssh(device, f"mkdir -p {shlex.quote(remote_dir)}",
                    timeout_sec=10,
                )
                _scp(str(temp_file), device, remote_file,
                    timeout_sec=30,
                )
                if device.get("restart_cmd"):
                    _ssh(device, _restart_video_service_command(device),
                        timeout_sec=10,
                    )
            except Exception as exc:
                errors.append(f"{device['name']}: {_clean_ssh_error(exc)}")
    finally:
        try:
            if temp_file.exists():
                temp_file.unlink()
        except OSError:
            pass

    if errors:
        db.create_video_history(
            source_type="manual",
            action="envio_masivo",
            status="error",
            filename=safe_name,
            detail="; ".join(errors),
        )
        return RedirectResponse(url="/?err=" + "; ".join(errors), status_code=303)
    db.create_video_history(
        source_type="manual",
        action="envio_masivo",
        status="ok",
        filename=safe_name,
        detail=f"Enviado a {len(selected_devices)} dispositivo(s)",
    )
    return RedirectResponse(url="/?msg=Envio masivo completado", status_code=303)


@app.post("/devices/{device_id}/reboot")
def reboot_device(device_id: int, user: dict = Depends(_get_current_user)):
    device = _device_by_id(device_id, user)
    error = _run_device_control(device, _reboot_command(), timeout_sec=8)
    if error:
        return RedirectResponse(url=f"/?err={device['name']}: {error}", status_code=303)
    return RedirectResponse(url=f"/?msg=Reinicio enviado a {device['name']}", status_code=303)


@app.post("/devices/reboot-all")
def reboot_devices_bulk(
    device_ids: List[int] = Form(...),
    user: dict = Depends(_get_current_user),
):
    visible = {device["id"]: device for device in _visible_devices_for_user(user)}
    selected = [visible.get(int(device_id)) for device_id in device_ids]
    selected = [device for device in selected if device is not None]
    if not selected:
        return RedirectResponse(url="/?err=No hay dispositivos validos para reiniciar", status_code=303)

    errors: List[str] = []
    for device in selected:
        error = _run_device_control(device, _reboot_command(), timeout_sec=8)
        if error:
            errors.append(f"{device['name']}: {error}")

    if errors:
        return RedirectResponse(url="/?err=" + "; ".join(errors), status_code=303)
    return RedirectResponse(url="/?msg=Reinicio masivo enviado", status_code=303)


@app.post("/devices/{device_id}/reload-service")
def reload_device_service(device_id: int, user: dict = Depends(_get_current_user)):
    device = _device_by_id(device_id, user)
    error = _run_device_control(device, _service_reload_command(device), timeout_sec=10)
    if error:
        return RedirectResponse(url=f"/?err={device['name']}: {error}", status_code=303)
    return RedirectResponse(url=f"/?msg=Servicio recargado en {device['name']}", status_code=303)


@app.post("/devices/reload-service-all")
def reload_devices_service_bulk(
    device_ids: List[int] = Form(...),
    user: dict = Depends(_get_current_user),
):
    visible = {device["id"]: device for device in _visible_devices_for_user(user)}
    selected = [visible.get(int(device_id)) for device_id in device_ids]
    selected = [device for device in selected if device is not None]
    if not selected:
        return RedirectResponse(url="/?err=No hay dispositivos validos para recargar", status_code=303)

    errors: List[str] = []
    for device in selected:
        error = _run_device_control(device, _service_reload_command(device), timeout_sec=10)
        if error:
            errors.append(f"{device['name']}: {error}")

    if errors:
        return RedirectResponse(url="/?err=" + "; ".join(errors), status_code=303)
    return RedirectResponse(url="/?msg=Recarga de servicio enviada", status_code=303)


def _normalize_ssh_result(result) -> tuple[bool, str]:
    if isinstance(result, str):
        return True, result
    return False, ""


def _infer_video_service(device: dict) -> str:
    restart_cmd = (device.get("restart_cmd") or "").strip()
    if not restart_cmd:
        return "video_looper"

    parts = shlex.split(restart_cmd)
    if "supervisorctl" in parts and "restart" in parts:
        idx = parts.index("restart")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if "restart" in parts:
        idx = parts.index("restart")
        if idx + 1 < len(parts):
            return parts[idx + 1]

    return "video_looper"


_SYSTEMD_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@-]+(?:\.service)?$")


def _sanitize_systemd_unit(unit: str, default: str = "video_looper") -> str:
    candidate = (unit or "").strip()
    if not candidate:
        return default
    if not _SYSTEMD_UNIT_RE.fullmatch(candidate):
        return default
    return candidate


def _restart_video_service_command(device: dict) -> str:
    unit = _sanitize_systemd_unit(_infer_video_service(device), default="video_looper")
    return f"sudo -n systemctl restart {shlex.quote(unit)}"


def _service_reload_command(device: dict) -> str:
    # Seguridad: jamás ejecutar strings arbitrarios provenientes de DB/usuario.
    # Usamos el campo `restart_cmd` sólo para inferir el nombre de la unidad.
    return _restart_video_service_command(device)


def _reboot_command() -> str:
    return "sudo -n reboot"


def _clean_ssh_error(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        if stderr:
            return stderr.splitlines()[-1]
        if stdout:
            return stdout.splitlines()[-1]
        return f"SSH devolvio codigo {exc.returncode}"
    return str(exc)


_ENV_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _sh_single_quote(value: str) -> str:
    return "'" + (value or "").replace("'", "'\"'\"'") + "'"


def _format_env_value(value: Any) -> str:
    if value is None:
        return "''"

    if isinstance(value, bool):
        return "1" if value else "0"

    if isinstance(value, (int, float)):
        return str(value)

    text = str(value)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text.strip()):
        return text.strip()
    if text.strip() in {"0", "1"}:
        return text.strip()

    return _sh_single_quote(text)


def _parse_env_value(raw: str) -> str:
    value = (raw or "").strip()
    if len(value) >= 2 and ((value[0] == value[-1] == "'") or (value[0] == value[-1] == '"')):
        return value[1:-1]
    return value


def _parse_env_file(text: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_ASSIGN_RE.match(line)
        if not match:
            continue
        key = match.group(1)
        raw_value = match.group(2)
        parsed[key] = _parse_env_value(raw_value)
    return parsed


def _update_env_file_content(original_text: str, updates: Dict[str, Any]) -> str:
    original_lines = (original_text or "").splitlines()
    out_lines: List[str] = []
    updated_keys = set(updates.keys())
    seen: set[str] = set()

    for line in original_lines:
        match = _ENV_ASSIGN_RE.match(line)
        if not match:
            out_lines.append(line)
            continue

        key = match.group(1)
        if key not in updated_keys:
            out_lines.append(line)
            continue

        out_lines.append(f"{key}={_format_env_value(updates[key])}")
        seen.add(key)

    for key, value in updates.items():
        if key in seen:
            continue
        out_lines.append(f"{key}={_format_env_value(value)}")

    normalized = "\n".join(out_lines).rstrip("\n") + "\n"
    if not normalized.strip():
        normalized = "\n"
    return normalized


def _read_remote_radio_env_text(device: dict, timeout_sec: int = 10) -> str:
    cmd = (
        f"f={shlex.quote(RADIO_ENV_FILE)}; "
        "(sudo -n cat \"$f\" 2>/dev/null || cat \"$f\" 2>/dev/null || true)"
    )
    raw_output = _ssh(device, cmd,
        timeout_sec=timeout_sec,
    )
    ok, output = _normalize_ssh_result(raw_output)
    if not ok:
        raise RuntimeError("Respuesta invalida del comando SSH")
    return output


def _read_remote_radio_status(device: dict, timeout_sec: int = 10) -> Dict[str, Any]:
    cmd = (
        f"svc={shlex.quote(RADIO_SERVICE_NAME)}; "
        "active=$(systemctl is-active \"$svc\" 2>/dev/null || true); "
        "enabled=$(systemctl is-enabled \"$svc\" 2>/dev/null || true); "
        "[ -z \"$active\" ] && active='unknown'; "
        "[ -z \"$enabled\" ] && enabled='unknown'; "
        "echo '__KV__|active|'\"$active\"; "
        "echo '__KV__|enabled|'\"$enabled\"; "
        "echo '__LOG__'; "
        "journalctl -u \"$svc\" -n 80 --no-pager 2>/dev/null | sed 's/\r$//' || true"
    )
    raw_output = _ssh(device, cmd,
        timeout_sec=timeout_sec,
    )
    ok, output = _normalize_ssh_result(raw_output)
    if not ok:
        raise RuntimeError("Respuesta invalida del comando SSH")

    kv: Dict[str, str] = {"active": "unknown", "enabled": "unknown"}
    logs = ""
    if "__LOG__" in output:
        header, logs = output.split("__LOG__", 1)
    else:
        header = output

    for line in header.splitlines():
        if not line.startswith("__KV__|"):
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        key = parts[1].strip()
        value = parts[2].strip()
        if key:
            kv[key] = value

    return {"active": kv.get("active", "unknown"), "enabled": kv.get("enabled", "unknown"), "logs": logs.strip("\n")}


def _read_remote_normalizer_status(device: dict, timeout_sec: int = 10) -> Dict[str, Any]:
    cmd = (
        f"svc={shlex.quote(NORMALIZER_SERVICE_NAME)}; "
        f"tmr={shlex.quote(NORMALIZER_TIMER_NAME)}; "
        "s_active=$(systemctl is-active \"$svc\" 2>/dev/null || true); "
        "s_enabled=$(systemctl is-enabled \"$svc\" 2>/dev/null || true); "
        "t_active=$(systemctl is-active \"$tmr\" 2>/dev/null || true); "
        "t_enabled=$(systemctl is-enabled \"$tmr\" 2>/dev/null || true); "
        "[ -z \"$s_active\" ] && s_active='unknown'; "
        "[ -z \"$s_enabled\" ] && s_enabled='unknown'; "
        "[ -z \"$t_active\" ] && t_active='unknown'; "
        "[ -z \"$t_enabled\" ] && t_enabled='unknown'; "
        "echo '__KV__|service_active|'\"$s_active\"; "
        "echo '__KV__|service_enabled|'\"$s_enabled\"; "
        "echo '__KV__|timer_active|'\"$t_active\"; "
        "echo '__KV__|timer_enabled|'\"$t_enabled\"; "
        "echo '__LOG__'; "
        "journalctl -u \"$svc\" -n 120 --no-pager 2>/dev/null | sed 's/\r$//' || true"
    )

    raw_output = _ssh(device, cmd,
        timeout_sec=timeout_sec,
    )
    ok, output = _normalize_ssh_result(raw_output)
    if not ok:
        raise RuntimeError("Respuesta invalida del comando SSH")

    kv: Dict[str, str] = {
        "service_active": "unknown",
        "service_enabled": "unknown",
        "timer_active": "unknown",
        "timer_enabled": "unknown",
    }
    logs = ""
    if "__LOG__" in output:
        header, logs = output.split("__LOG__", 1)
    else:
        header = output

    for line in header.splitlines():
        if not line.startswith("__KV__|"):
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        key = parts[1].strip()
        value = parts[2].strip()
        if key:
            kv[key] = value

    return {
        "service": {
            "name": NORMALIZER_SERVICE_NAME,
            "active": kv.get("service_active", "unknown"),
            "enabled": kv.get("service_enabled", "unknown"),
        },
        "timer": {
            "name": NORMALIZER_TIMER_NAME,
            "active": kv.get("timer_active", "unknown"),
            "enabled": kv.get("timer_enabled", "unknown"),
        },
        "logs": logs.strip("\n"),
    }


def _device_by_id(device_id: int, user: dict) -> dict:
    devices = {device["id"]: device for device in _visible_devices_for_user(user)}
    device = devices.get(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado")
    return device


def _safe_remote_filename(filename: str) -> str:
    clean_name = Path(filename or "").name.strip()
    if not clean_name or clean_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Nombre de archivo invalido")
    if clean_name != filename.strip():
        raise HTTPException(status_code=400, detail="Nombre de archivo invalido")
    return clean_name


def _run_device_control(device: dict, command: str, timeout_sec: int = 12) -> Optional[str]:
    try:
        _ssh(device, command,
            timeout_sec=timeout_sec,
        )
        return None
    except Exception as exc:
        return _clean_ssh_error(exc)


def _allowed_video_extension(filename: str) -> bool:
    extension = Path(filename).suffix.lower()
    return extension in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


def _read_remote_looper_log(device: dict, lines: int = 200, timeout_sec: int = 8) -> Dict[str, Any]:
    safe_lines = max(20, min(int(lines), 1500))
    primary_log = DEFAULT_DEVICE_LOG_PATH
    fallback_log = "/var/log/video_looper.log"

    cmd = (
        f"n={safe_lines}; "
        f"p1={shlex.quote(primary_log)}; "
        f"p2={shlex.quote(fallback_log)}; "
        "if [ -f \"$p1\" ]; then "
        "tail -n \"$n\" \"$p1\" | sed 's/\r$//'; "
        "printf '\n__LOG_PATH__|%s\n' \"$p1\"; "
        "elif [ -f \"$p2\" ]; then "
        "tail -n \"$n\" \"$p2\" | sed 's/\r$//'; "
        "printf '\n__LOG_PATH__|%s\n' \"$p2\"; "
        "else "
        "journalctl -u video_looper -n \"$n\" --no-pager 2>/dev/null | sed 's/\r$//' || true; "
        "printf '\n__LOG_PATH__|journalctl:video_looper\n'; "
        "fi"
    )

    raw_output = _ssh(device, cmd,
        timeout_sec=timeout_sec,
    )
    ok, output = _normalize_ssh_result(raw_output)
    if not ok:
        raise RuntimeError("Respuesta invalida del comando SSH")

    marker = "\n__LOG_PATH__|"
    log_path = "desconocido"
    content = output
    if marker in output:
        content, suffix = output.rsplit(marker, 1)
        log_path = suffix.strip().splitlines()[0] if suffix.strip() else log_path

    return {"log_path": log_path, "content": content.strip("\n")}


def _list_remote_files(device: dict, timeout_sec: int = 8) -> List[Dict[str, Any]]:
    remote_path = (device.get("remote_path") or DEFAULT_REMOTE_PATH).strip()
    display_remote_path = remote_path.replace('"', "")

    cmd = (
        f"dir={shlex.quote(remote_path)}; "
        "if [ -d \"$dir\" ]; then "
        "for f in \"$dir\"/*; do "
        "[ -f \"$f\" ] || continue; "
        "name=$(basename \"$f\"); "
        "size=$(wc -c < \"$f\" | tr -d ' '); "
        "modified=$(date -r \"$f\" '+%Y-%m-%d %H:%M'); "
        "printf '%s|%s|%s\\n' \"$name\" \"$size\" \"$modified\"; "
        "done | sort; "
        f"else echo '__ERROR__|Ruta no existe: {display_remote_path}'; fi"
    )

    raw_output = _ssh(device, cmd,
        timeout_sec=timeout_sec,
    )

    ok, output = _normalize_ssh_result(raw_output)
    if not ok:
        raise RuntimeError("Respuesta invalida del comando SSH")

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if lines and lines[0].startswith("__ERROR__|"):
        raise RuntimeError(lines[0].split("|", 1)[1])

    files: List[Dict[str, Any]] = []
    for line in lines:
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        try:
            size_bytes = int(parts[1])
        except ValueError:
            size_bytes = 0
        files.append({"name": parts[0], "size_bytes": size_bytes, "modified": parts[2]})

    return files

@app.get("/api/devices/{device_id}/files")
def list_device_files(device_id: int, user: dict = Depends(_get_current_user)):
    device = _device_by_id(device_id, user)
    remote_path = (device.get("remote_path") or DEFAULT_REMOTE_PATH).strip()

    try:
        files = _list_remote_files(device, timeout_sec=8)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo consultar el dispositivo: {_clean_ssh_error(exc)}"},
        )

    return {
        "ok": True,
        "device": {
            "id": device["id"],
            "name": device["name"],
            "host": device["host"],
            "remote_path": remote_path,
        },
        "files": files,
    }


@app.get("/api/devices/{device_id}/logs")
def get_device_logs(
    device_id: int,
    lines: int = 200,
    user: dict = Depends(_get_current_user),
):
    device = _device_by_id(device_id, user)

    try:
        payload = _read_remote_looper_log(device, lines=lines, timeout_sec=10)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo leer log remoto: {_clean_ssh_error(exc)}"},
        )

    return {
        "ok": True,
        "device": {"id": device["id"], "name": device["name"], "host": device["host"]},
        "log_path": payload["log_path"],
        "content": payload["content"],
    }


@app.post("/api/devices/{device_id}/files/upload")
def upload_device_file(
    device_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(_get_current_user),
):
    device = _device_by_id(device_id, user)

    if not file.filename:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Archivo invalido"})

    safe_name = _safe_remote_filename(file.filename)
    if not _allowed_video_extension(safe_name):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Formato no permitido. Usa mp4, mov, mkv, avi, webm o m4v"},
        )

    remote_dir = (device.get("remote_path") or DEFAULT_REMOTE_PATH).strip().rstrip("/")
    remote_target = f"{remote_dir}/{safe_name}"

    temp_dir = UPLOADS_DIR / "_remote_uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / f"{uuid.uuid4().hex}_{safe_name}"

    try:
        with temp_file.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)

        _ssh(device, f"mkdir -p {shlex.quote(remote_dir)}",
            timeout_sec=10,
        )
        _scp(str(temp_file), device, remote_target,
            timeout_sec=20,
        )
    except Exception as exc:
        db.create_video_history(
            source_type="manual",
            action="subida_rpi",
            status="error",
            filename=safe_name,
            device_id=int(device["id"]),
            device_name=device["name"],
            detail=_clean_ssh_error(exc),
        )
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo subir el archivo: {_clean_ssh_error(exc)}"},
        )
    finally:
        try:
            if temp_file.exists():
                temp_file.unlink()
        except OSError:
            pass

    db.create_video_history(
        source_type="manual",
        action="subida_rpi",
        status="ok",
        filename=safe_name,
        device_id=int(device["id"]),
        device_name=device["name"],
        detail="Subido desde gestor de contenido",
    )
    return {"ok": True, "msg": "Video subido", "filename": safe_name}


@app.post("/api/devices/{device_id}/files/delete")
def delete_device_file(
    device_id: int,
    filename: str = Form(...),
    user: dict = Depends(_get_current_user),
):
    device = _device_by_id(device_id, user)
    safe_name = _safe_remote_filename(filename)
    remote_dir = (device.get("remote_path") or DEFAULT_REMOTE_PATH).strip().rstrip("/")
    remote_target = f"{remote_dir}/{safe_name}"

    cmd = (
        f"target={shlex.quote(remote_target)}; "
        "if [ -f \"$target\" ]; then rm -f \"$target\"; "
        "else echo '__ERROR__|Archivo no existe'; fi"
    )

    try:
        output = _ssh(device, cmd,
            timeout_sec=8,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo eliminar: {_clean_ssh_error(exc)}"},
        )

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if lines and lines[0].startswith("__ERROR__|"):
        return JSONResponse(status_code=400, content={"ok": False, "error": lines[0].split("|", 1)[1]})

    return {"ok": True, "msg": "Archivo eliminado"}


@app.post("/api/devices/{device_id}/files/reorder")
def reorder_device_files(
    device_id: int,
    payload: Dict[str, Any] = Body(...),
    user: dict = Depends(_get_current_user),
):
    device = _device_by_id(device_id, user)
    requested = payload.get("filenames")
    if not isinstance(requested, list):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Payload invalido"})

    safe_requested = []
    seen_names = set()
    for raw_name in requested:
        if not isinstance(raw_name, str):
            continue
        clean_name = _safe_remote_filename(raw_name)
        if clean_name in seen_names:
            continue
        seen_names.add(clean_name)
        safe_requested.append(clean_name)

    try:
        existing_files = _list_remote_files(device, timeout_sec=8)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo leer archivos: {_clean_ssh_error(exc)}"},
        )

    if not existing_files:
        return {"ok": True, "msg": "Sin archivos para ordenar"}

    existing_names = [entry["name"] for entry in existing_files]
    set_existing = set(existing_names)
    ordered_names = [name for name in safe_requested if name in set_existing]
    ordered_names.extend([name for name in existing_names if name not in set(ordered_names)])

    rename_pairs = []
    temp_pairs = []
    for index, original_name in enumerate(ordered_names, start=1):
        stem = Path(original_name).stem
        extension = Path(original_name).suffix
        cleaned_stem = stem
        if len(cleaned_stem) > 4 and cleaned_stem[:3].isdigit() and cleaned_stem[3] in {"_", "-"}:
            cleaned_stem = cleaned_stem[4:]
        final_name = f"{index:03d}_{cleaned_stem}{extension}"
        tmp_name = f".__vdtmp_{uuid.uuid4().hex[:8]}_{index}{extension}"
        temp_pairs.append((original_name, tmp_name))
        rename_pairs.append((tmp_name, final_name))

    remote_dir = (device.get("remote_path") or DEFAULT_REMOTE_PATH).strip().rstrip("/")
    cmd_parts = [f"cd {shlex.quote(remote_dir)}"]
    for original_name, tmp_name in temp_pairs:
        cmd_parts.append(f"mv -f {shlex.quote(original_name)} {shlex.quote(tmp_name)}")
    for tmp_name, final_name in rename_pairs:
        cmd_parts.append(f"mv -f {shlex.quote(tmp_name)} {shlex.quote(final_name)}")
    cmd = " && ".join(cmd_parts)

    try:
        _ssh(device, cmd,
            timeout_sec=12,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo reordenar: {_clean_ssh_error(exc)}"},
        )

    return {"ok": True, "msg": "Orden actualizado"}


@app.post("/api/devices/{device_id}/files/duplicate")
def duplicate_device_file(
    device_id: int,
    payload: Dict[str, Any] = Body(...),
    user: dict = Depends(_get_current_user),
):
    source_device = _device_by_id(device_id, user)
    filename = payload.get("filename")
    target_ids = payload.get("target_device_ids")

    if not isinstance(filename, str):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Archivo invalido"})
    if not isinstance(target_ids, list):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Destinos invalidos"})

    safe_name = _safe_remote_filename(filename)
    visible = {device["id"]: device for device in _visible_devices_for_user(user)}
    targets = []
    for raw_id in target_ids:
        try:
            target_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if target_id == int(source_device["id"]):
            continue
        device = visible.get(target_id)
        if device:
            targets.append(device)

    if not targets:
        return JSONResponse(status_code=400, content={"ok": False, "error": "No seleccionaste RPIs destino"})

    src_dir = (source_device.get("remote_path") or DEFAULT_REMOTE_PATH).rstrip("/")
    source_remote_file = f"{src_dir}/{safe_name}"

    temp_dir = UPLOADS_DIR / "_duplicate_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_local_file = temp_dir / f"{uuid.uuid4().hex}_{safe_name}"

    errors: List[str] = []
    try:
        _scp_dn(source_device, source_remote_file,
            str(temp_local_file),
            timeout_sec=20,
        )

        for target in targets:
            dst_dir = (target.get("remote_path") or DEFAULT_REMOTE_PATH).rstrip("/")
            dst_file = f"{dst_dir}/{safe_name}"
            try:
                _ssh(target, f"mkdir -p {shlex.quote(dst_dir)}",
                    timeout_sec=10,
                )
                _scp(str(temp_local_file), target, dst_file,
                    timeout_sec=20,
                )
            except Exception as exc:
                errors.append(f"{target['name']}: {_clean_ssh_error(exc)}")
    except Exception as exc:
        db.create_video_history(
            source_type="manual",
            action="duplicar_rpi",
            status="error",
            filename=safe_name,
            device_id=int(source_device["id"]),
            device_name=source_device["name"],
            detail=f"Origen {source_device['name']}: {_clean_ssh_error(exc)}",
        )
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo copiar desde origen: {_clean_ssh_error(exc)}"},
        )
    finally:
        try:
            if temp_local_file.exists():
                temp_local_file.unlink()
        except OSError:
            pass

    if errors:
        db.create_video_history(
            source_type="manual",
            action="duplicar_rpi",
            status="error",
            filename=safe_name,
            device_id=int(source_device["id"]),
            device_name=source_device["name"],
            detail="; ".join(errors),
        )
        return JSONResponse(status_code=502, content={"ok": False, "error": "; ".join(errors)})
    db.create_video_history(
        source_type="manual",
        action="duplicar_rpi",
        status="ok",
        filename=safe_name,
        device_id=int(source_device["id"]),
        device_name=source_device["name"],
        detail=f"Duplicado a {len(targets)} destino(s)",
    )
    return {"ok": True, "msg": "Archivo duplicado a destinos"}


@app.get("/api/devices/{device_id}/info")
def get_device_info(device_id: int, user: dict = Depends(_get_current_user)):
    device = _device_by_id(device_id, user)

    info = {
        "status": "Desconocido",
        "temperature": "--",
        "uptime": "--",
        "last_check": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "video_service": _infer_video_service(device),
        "video_path": (device.get("remote_path") or DEFAULT_REMOTE_PATH).strip(),
    }

    cmd = (
        "temp='--'; "
        "if command -v vcgencmd >/dev/null 2>&1; then "
        "raw=$(vcgencmd measure_temp 2>/dev/null | sed -E \"s/temp=([0-9.]+).*/\\1/\"); "
        "[ -n \"$raw\" ] && temp=\"${raw}°C\"; "
        "elif [ -f /sys/class/thermal/thermal_zone0/temp ]; then "
        "raw=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null); "
        "if [ -n \"$raw\" ]; then temp=$(awk \"BEGIN {printf \\\"%.1f°C\\\", $raw/1000}\"); fi; "
        "fi; "
        "up=$(uptime -p 2>/dev/null || true); "
        "[ -z \"$up\" ] && up='--'; "
        "printf 'temperature|%s\\n' \"$temp\"; "
        "printf 'uptime|%s\\n' \"$up\";"
    )

    try:
        raw_output = _ssh(device, cmd,
            timeout_sec=8,
        )
        ok, output = _normalize_ssh_result(raw_output)
        if ok:
            info["status"] = "Online"
            for line in output.splitlines():
                if "|" not in line:
                    continue
                key, value = line.split("|", 1)
                if key in {"temperature", "uptime"} and value.strip():
                    info[key] = value.strip()
            return {"ok": True, "device": {"id": device["id"], "name": device["name"]}, "info": info}
    except Exception as exc:
        return {
            "ok": True,
            "device": {"id": device["id"], "name": device["name"]},
            "info": info,
            "warning": f"No se pudo conectar por SSH: {_clean_ssh_error(exc)}",
        }

    return {"ok": True, "device": {"id": device["id"], "name": device["name"]}, "info": info}


@app.get("/api/devices/{device_id}/radio")
def get_device_radio(device_id: int, user: dict = Depends(_get_current_user)):
    device = _device_by_id(device_id, user)

    try:
        env_text = _read_remote_radio_env_text(device, timeout_sec=10)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo leer config de radio: {_clean_ssh_error(exc)}"},
        )

    parsed = _parse_env_file(env_text)

    def _as_bool(value: Optional[str]) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    try:
        status_payload = _read_remote_radio_status(device, timeout_sec=10)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo consultar estado de radio: {_clean_ssh_error(exc)}"},
        )

    mode = (parsed.get("VIDLOOP_RADIO_MODE") or "vidloop").strip().lower()
    if mode not in {"vidloop", "stream", "link"}:
        mode = "vidloop"

    payload = {
        "enabled": _as_bool(parsed.get("VIDLOOP_ENABLE_RADIO")),
        "mode": mode,
        "link_url": (parsed.get("VIDLOOP_RADIO_LINK_URL") or "").strip(),
        "stream_url": (parsed.get("VIDLOOP_RADIO_STREAM_URL") or "").strip(),
        "jingle_every": int(parsed.get("VIDLOOP_RADIO_JINGLE_EVERY") or 0) if str(parsed.get("VIDLOOP_RADIO_JINGLE_EVERY") or "").strip().isdigit() else 0,
        "beep_interval_min": int(parsed.get("VIDLOOP_RADIO_BEEP_INTERVAL_MIN") or 0) if str(parsed.get("VIDLOOP_RADIO_BEEP_INTERVAL_MIN") or "").strip().isdigit() else 0,
    }

    installed = bool(env_text.strip()) or status_payload.get("active") not in {"", "unknown"} or status_payload.get("enabled") not in {"", "unknown"}

    return {
        "ok": True,
        "device": {"id": device["id"], "name": device["name"], "host": device["host"]},
        "installed": installed,
        "config": payload,
        "service": {
            "name": RADIO_SERVICE_NAME,
            "active": status_payload.get("active", "unknown"),
            "enabled": status_payload.get("enabled", "unknown"),
        },
        "logs": status_payload.get("logs", ""),
    }


@app.post("/api/devices/{device_id}/radio")
def apply_device_radio(
    device_id: int,
    payload: Dict[str, Any] = Body(...),
    user: dict = Depends(_get_current_user),
):
    device = _device_by_id(device_id, user)

    enabled = bool(payload.get("enabled", False))
    mode = str(payload.get("mode", "vidloop")).strip().lower()
    if mode not in {"vidloop", "stream", "link"}:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Modo invalido"})

    link_url = str(payload.get("link_url", "")).strip()
    stream_url = str(payload.get("stream_url", "")).strip()

    try:
        jingle_every = int(payload.get("jingle_every") or 0)
        beep_interval_min = int(payload.get("beep_interval_min") or 0)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Valores numericos invalidos"})

    if jingle_every < 0 or jingle_every > 1440:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Jingle cada X: fuera de rango"})
    if beep_interval_min < 0 or beep_interval_min > 1440:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Pitido cada X: fuera de rango"})

    updates: Dict[str, Any] = {
        "VIDLOOP_ENABLE_RADIO": 1 if enabled else 0,
        "VIDLOOP_RADIO_MODE": mode,
        "VIDLOOP_RADIO_LINK_URL": link_url,
        "VIDLOOP_RADIO_STREAM_URL": stream_url,
        "VIDLOOP_RADIO_JINGLE_EVERY": jingle_every,
        "VIDLOOP_RADIO_BEEP_INTERVAL_MIN": beep_interval_min,
    }

    try:
        original_text = _read_remote_radio_env_text(device, timeout_sec=10)
    except Exception:
        original_text = ""

    if not original_text.strip():
        original_text = "# VIDLOOP Radio (gestionado por VIDLOOP-DASH)\n"

    new_text = _update_env_file_content(original_text, updates)
    b64 = base64.b64encode(new_text.encode("utf-8")).decode("ascii")

    write_cmd = (
        f"b64={shlex.quote(b64)}; "
        f"target={shlex.quote(RADIO_ENV_FILE)}; "
        "printf %s \"$b64\" | base64 -d | sudo -n tee \"$target\" >/dev/null"
    )
    restart_cmd = f"sudo -n systemctl restart {shlex.quote(RADIO_SERVICE_NAME)}"

    try:
        _ssh(device, write_cmd,
            timeout_sec=12,
        )
        _ssh(device, restart_cmd,
            timeout_sec=12,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo aplicar/reiniciar radio: {_clean_ssh_error(exc)}"},
        )

    try:
        status_payload = _read_remote_radio_status(device, timeout_sec=10)
    except Exception:
        status_payload = {"active": "unknown", "enabled": "unknown", "logs": ""}

    return {
        "ok": True,
        "msg": "Configuración aplicada y radio reiniciada",
        "service": {
            "name": RADIO_SERVICE_NAME,
            "active": status_payload.get("active", "unknown"),
            "enabled": status_payload.get("enabled", "unknown"),
        },
        "logs": status_payload.get("logs", ""),
    }


@app.post("/api/devices/{device_id}/radio/restart")
def restart_device_radio(device_id: int, user: dict = Depends(_get_current_user)):
    device = _device_by_id(device_id, user)
    cmd = f"sudo -n systemctl restart {shlex.quote(RADIO_SERVICE_NAME)}"
    try:
        _ssh(device, cmd,
            timeout_sec=12,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo reiniciar radio: {_clean_ssh_error(exc)}"},
        )

    try:
        status_payload = _read_remote_radio_status(device, timeout_sec=10)
    except Exception:
        status_payload = {"active": "unknown", "enabled": "unknown", "logs": ""}

    return {
        "ok": True,
        "msg": "Radio reiniciada",
        "service": {
            "name": RADIO_SERVICE_NAME,
            "active": status_payload.get("active", "unknown"),
            "enabled": status_payload.get("enabled", "unknown"),
        },
        "logs": status_payload.get("logs", ""),
    }


@app.get("/api/devices/{device_id}/normalizer")
def get_device_normalizer(device_id: int, user: dict = Depends(_get_current_user)):
    device = _device_by_id(device_id, user)
    try:
        payload = _read_remote_normalizer_status(device, timeout_sec=10)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo consultar normalizador: {_clean_ssh_error(exc)}"},
        )

    installed = (
        (payload.get("service") or {}).get("active") not in {"", "unknown"}
        or (payload.get("service") or {}).get("enabled") not in {"", "unknown"}
        or (payload.get("timer") or {}).get("active") not in {"", "unknown"}
        or (payload.get("timer") or {}).get("enabled") not in {"", "unknown"}
    )

    return {
        "ok": True,
        "device": {"id": device["id"], "name": device["name"], "host": device["host"]},
        "installed": installed,
        "service": payload.get("service"),
        "timer": payload.get("timer"),
        "logs": payload.get("logs", ""),
    }


@app.post("/api/devices/{device_id}/normalizer/run")
def run_device_normalizer(device_id: int, user: dict = Depends(_get_current_user)):
    device = _device_by_id(device_id, user)
    cmd = f"sudo -n systemctl start {shlex.quote(NORMALIZER_SERVICE_NAME)}"
    try:
        _ssh(device, cmd,
            timeout_sec=12,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"No se pudo ejecutar normalizador: {_clean_ssh_error(exc)}"},
        )

    try:
        payload = _read_remote_normalizer_status(device, timeout_sec=10)
    except Exception:
        payload = {"service": None, "timer": None, "logs": ""}

    return {
        "ok": True,
        "msg": "Normalizador ejecutado (start enviado)",
        "service": payload.get("service"),
        "timer": payload.get("timer"),
        "logs": payload.get("logs", ""),
    }


# ============================================================================
# Endpoints de Programación / Schedules
# ============================================================================

@app.post("/schedules/add")
def add_schedule_endpoint(
    device_id: int = Form(...),
    content_type: str = Form(...),
    content_reference: str = Form(""),
    time_start: str = Form(...),
    time_end: str = Form(...),
    days_of_week: str = Form(...),
    user: dict = Depends(_get_current_user),
):
    """Crea una nueva programación"""
    if user["role"] not in ["admin", "owner"]:
        return RedirectResponse(url="/?err=No tenés permisos para crear programaciones", status_code=303)
    
    try:
        db.add_schedule({
            "device_id": device_id,
            "content_type": content_type,
            "content_reference": content_reference,
            "time_start": time_start,
            "time_end": time_end,
            "days_of_week": days_of_week,
            "enabled": 1,
        })
        return RedirectResponse(url="/?msg=Programación creada exitosamente", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/?err=Error al crear programación: {exc}", status_code=303)


@app.post("/schedules/{schedule_id}/update")
def update_schedule_endpoint(
    schedule_id: int,
    device_id: int = Form(...),
    content_type: str = Form(...),
    content_reference: str = Form(""),
    time_start: str = Form(...),
    time_end: str = Form(...),
    days_of_week: str = Form(...),
    user: dict = Depends(_get_current_user),
):
    """Actualiza una programación existente"""
    if user["role"] not in ["admin", "owner"]:
        return RedirectResponse(url="/?err=No tenés permisos para editar programaciones", status_code=303)
    
    try:
        db.update_schedule(schedule_id, {
            "device_id": device_id,
            "content_type": content_type,
            "content_reference": content_reference,
            "time_start": time_start,
            "time_end": time_end,
            "days_of_week": days_of_week,
            "enabled": 1,
        })
        return RedirectResponse(url="/?msg=Programación actualizada exitosamente", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/?err=Error al actualizar programación: {exc}", status_code=303)


@app.post("/schedules/{schedule_id}/delete")
def delete_schedule_endpoint(
    schedule_id: int,
    user: dict = Depends(_get_current_user),
):
    """Elimina una programación"""
    if user["role"] not in ["admin", "owner"]:
        return RedirectResponse(url="/?err=No tenés permisos para eliminar programaciones", status_code=303)
    
    try:
        db.delete_schedule(schedule_id)
        return RedirectResponse(url="/?msg=Programación eliminada", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/?err=Error al eliminar programación: {exc}", status_code=303)


@app.post("/schedules/{schedule_id}/toggle")
def toggle_schedule_endpoint(
    schedule_id: int,
    enabled: bool = Form(...),
    user: dict = Depends(_get_current_user),
):
    """Activa/desactiva una programación"""
    if user["role"] not in ["admin", "owner"]:
        return RedirectResponse(url="/?err=No tenés permisos para modificar programaciones", status_code=303)
    
    try:
        db.toggle_schedule(schedule_id, enabled)
        status_text = "activada" if enabled else "desactivada"
        return RedirectResponse(url=f"/?msg=Programación {status_text}", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/?err=Error al cambiar estado: {exc}", status_code=303)


@app.get("/api/schedules")
def get_schedules_api(
    device_id: Optional[int] = None,
    user: dict = Depends(_get_current_user),
):
    """Obtiene las programaciones (API JSON)"""
    try:
        schedules = db.get_schedules(device_id=device_id)
        return {"ok": True, "schedules": schedules}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
