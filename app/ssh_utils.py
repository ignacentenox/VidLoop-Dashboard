import os
import shlex
import shutil
import subprocess
import time
import uuid
from typing import List, Optional

# Tiempo de espera entre reintentos (segundos)
_RETRY_SLEEP = 1.5


def _ssh_base_args(
    user: str,
    host: str,
    port: int,
    ssh_key_path: Optional[str],
    ssh_password: Optional[str],
    connect_timeout: int,
) -> List[str]:
    args = ["ssh", "-p", str(port)]
    args.extend(["-o", f"ConnectTimeout={connect_timeout}"])

    if ssh_password:
        args.extend(
            [
                "-o",
                "BatchMode=no",
                "-o",
                "PubkeyAuthentication=no",
                "-o",
                "PreferredAuthentications=password,keyboard-interactive",
                "-o",
                "NumberOfPasswordPrompts=1",
                "-o",
                "StrictHostKeyChecking=accept-new",
            ]
        )
    else:
        args.extend(
            [
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "NumberOfPasswordPrompts=0",
            ]
        )
    if ssh_key_path:
        args.extend(["-i", ssh_key_path])
    args.append(f"{user}@{host}")
    return args


def _with_password_args(ssh_password: Optional[str]) -> List[str]:
    if not ssh_password:
        return []

    if not shutil.which("sshpass"):
        raise RuntimeError(
            "Este dispositivo usa password SSH y falta 'sshpass' en el sistema. "
            "Instalalo o configura 'ssh_key_path'."
        )

    return ["sshpass", "-p", ssh_password]


def _scp_args(
    user: str,
    host: str,
    port: int,
    ssh_key_path: Optional[str],
    ssh_password: Optional[str],
    timeout_sec: int,
) -> List[str]:
    args = _with_password_args(ssh_password)
    args.extend(["scp", "-P", str(port)])
    args.extend(["-o", f"ConnectTimeout={max(1, int(timeout_sec))}"])
    if ssh_password:
        args.extend(
            [
                "-o",
                "BatchMode=no",
                "-o",
                "PubkeyAuthentication=no",
                "-o",
                "PreferredAuthentications=password,keyboard-interactive",
                "-o",
                "NumberOfPasswordPrompts=1",
                "-o",
                "StrictHostKeyChecking=accept-new",
            ]
        )
    else:
        args.extend(
            [
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "NumberOfPasswordPrompts=0",
            ]
        )
    if ssh_key_path:
        args.extend(["-i", ssh_key_path])
    return args


def _hosts_to_try(host: str, host_fallback: Optional[str]) -> List[str]:
    """Devuelve la lista de hosts a intentar: VPN primero, luego LAN."""
    hosts = [host]
    if host_fallback and host_fallback.strip() and host_fallback.strip() != host:
        hosts.append(host_fallback.strip())
    return hosts


def run_ssh_command(
    user: str,
    host: str,
    port: int,
    ssh_key_path: Optional[str],
    ssh_password: Optional[str],
    command: str,
    timeout_sec: int = 12,
    host_fallback: Optional[str] = None,
    retries: int = 2,
) -> str:
    """
    Ejecuta un comando SSH con reintentos y fallback a IP LAN.

    Intenta primero `host` (VPN). Si falla todos los reintentos,
    intenta `host_fallback` (LAN local) con la misma cantidad de reintentos.
    """
    last_exc: Optional[Exception] = None
    ct = max(1, int(timeout_sec))

    for attempt_host in _hosts_to_try(host, host_fallback):
        for _ in range(max(1, retries)):
            try:
                args = _with_password_args(ssh_password)
                args.extend(_ssh_base_args(user, attempt_host, port, ssh_key_path, ssh_password, ct))
                args.append(command)
                completed = subprocess.run(
                    args,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=ct + 2,
                )
                return completed.stdout
            except Exception as exc:
                last_exc = exc
                time.sleep(_RETRY_SLEEP)

    raise last_exc  # type: ignore[misc]


def _run_scp_upload_direct(
    local_path: str,
    user: str,
    host: str,
    port: int,
    ssh_key_path: Optional[str],
    ssh_password: Optional[str],
    remote_path: str,
    timeout_sec: int,
) -> None:
    """SCP directo sin atomicidad (uso interno)."""
    args = _scp_args(user, host, port, ssh_key_path, ssh_password, timeout_sec)
    args.extend([local_path, f"{user}@{host}:{remote_path}"])
    subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout_sec)) + 5,
    )


def _run_scp_upload_atomic(
    local_path: str,
    user: str,
    host: str,
    port: int,
    ssh_key_path: Optional[str],
    ssh_password: Optional[str],
    remote_path: str,
    timeout_sec: int,
) -> None:
    """
    SCP atómico: sube a /tmp/.<uuid>_filename, luego hace mv al destino.
    Si la conexión se corta a mitad del upload, el archivo original no se toca.
    """
    tmp_name = f".vidloop_{uuid.uuid4().hex}_{os.path.basename(remote_path)}"
    tmp_remote = f"/tmp/{tmp_name}"

    # Paso 1: subir al temporal
    _run_scp_upload_direct(
        local_path, user, host, port, ssh_key_path, ssh_password, tmp_remote, timeout_sec
    )

    # Paso 2: mv atómico al destino final (mkdir -p por si no existe el dir)
    remote_dir = os.path.dirname(remote_path) or "."
    mv_cmd = (
        f"mkdir -p {shlex.quote(remote_dir)} && "
        f"mv {shlex.quote(tmp_remote)} {shlex.quote(remote_path)}"
    )
    run_ssh_command(
        user, host, port, ssh_key_path, ssh_password, mv_cmd, timeout_sec=20
    )


def run_scp(
    local_path: str,
    user: str,
    host: str,
    port: int,
    ssh_key_path: Optional[str],
    ssh_password: Optional[str],
    remote_path: str,
    timeout_sec: int = 20,
    host_fallback: Optional[str] = None,
    retries: int = 2,
    atomic: bool = True,
) -> None:
    """
    SCP con reintentos, fallback VPN→LAN y escritura atómica.

    Con atomic=True (defecto): sube a /tmp y hace mv — el archivo
    destino nunca queda corrupto si se corta la transferencia.
    """
    last_exc: Optional[Exception] = None
    uploader = _run_scp_upload_atomic if atomic else _run_scp_upload_direct

    for attempt_host in _hosts_to_try(host, host_fallback):
        for _ in range(max(1, retries)):
            try:
                uploader(
                    local_path, user, attempt_host, port,
                    ssh_key_path, ssh_password, remote_path, timeout_sec,
                )
                return
            except Exception as exc:
                last_exc = exc
                time.sleep(_RETRY_SLEEP)

    raise last_exc  # type: ignore[misc]


def run_scp_download(
    user: str,
    host: str,
    port: int,
    ssh_key_path: Optional[str],
    ssh_password: Optional[str],
    remote_path: str,
    local_path: str,
    timeout_sec: int = 20,
    host_fallback: Optional[str] = None,
    retries: int = 2,
) -> None:
    """SCP download con reintentos y fallback VPN→LAN."""
    last_exc: Optional[Exception] = None

    for attempt_host in _hosts_to_try(host, host_fallback):
        for _ in range(max(1, retries)):
            try:
                args = _scp_args(user, attempt_host, port, ssh_key_path, ssh_password, timeout_sec)
                args.extend([f"{user}@{attempt_host}:{remote_path}", local_path])
                subprocess.run(
                    args,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=max(1, int(timeout_sec)) + 5,
                )
                return
            except Exception as exc:
                last_exc = exc
                time.sleep(_RETRY_SLEEP)

    raise last_exc  # type: ignore[misc]
