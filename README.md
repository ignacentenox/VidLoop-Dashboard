# VIDLOOP-DASH

Dashboard centralizado para administrar Raspberry Pi con reproducción de video desde un servidor central.

## ✨ Características Principales

- 🎥 **Generación de videos** a partir de imágenes con transiciones personalizables
- 📤 **Envío masivo** de videos a múltiples Raspberry Pi simultáneamente
- 📅 **Programación de contenido** por horarios (videos locales, streams en vivo de YouTube/RTMP, loops)
- 🖥️ **Gestión remota** de dispositivos (reinicio, recarga de servicio, info del sistema)
- 📊 **Historial completo** de videos generados y envíos realizados
- 👥 **Sistema de usuarios** con roles (admin, operador, dueño)
- 🌙 **Modo oscuro** por defecto
- 🔧 **Modo de mantenimiento** configurable

## 📅 Programación de Contenido (NUEVO)

Permite programar automáticamente qué contenido se reproduce en cada RPI según horario y día:

- **Videos específicos** en horarios determinados
- **Transmisiones en vivo** de YouTube (ej: `https://youtu.be/Is0yasFyVmM`) o RTMP
- **Loop de todos los videos** locales
- **Retorno automático** al contenido local cuando finaliza el horario programado

Ver [GUIA_PROGRAMACION.md](GUIA_PROGRAMACION.md) para instrucciones detalladas.

## Requisitos
- Python 3.10+
- FFmpeg instalado en el servidor
- Acceso SSH a cada Raspberry (usuario, host, llave)

## Compatibilidad Python

Para evitar errores de autenticación con `passlib`, este proyecto fija:

- `passlib[bcrypt]==1.7.4`
- `bcrypt==4.0.1`

Si ves errores tipo `error reading bcrypt version` o `module 'bcrypt' has no attribute '__about__'`, reinstalá dependencias con el entorno activo:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## Instalacion
```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Ejecutar
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Alternativa recomendada para asegurar el intérprete del entorno:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Acceso
- Se usa autenticacion basica HTTP (el navegador pedira usuario y clave).
- Si no hay usuarios creados, se genera un admin por defecto:
	- Usuario: admin
	- Password: admin123
- Podes definirlos antes de iniciar:
	- `ADMIN_USER` y `ADMIN_PASS`

## Uso con IPs VPN (WireGuard)

- Este repo incluye scripts auxiliares para conectar las Raspberry por WireGuard y operar el dashboard usando IPs VPN.

Flujo mínimo:

1. En la Raspberry, corré `wireguard_setup_rpi.sh` para instalar WireGuard, generar claves y crear `wg0.conf`.
2. Copiá la public key que devuelve la Raspberry.
3. En el VPS, corré `wireguard_add_peer_vps.sh` para dar de alta ese peer.
4. Cuando la VPN esté operativa, actualizá los hosts del dashboard a las IPs VPN.

Ejemplo rápido:

```bash
# En la Raspberry
sudo bash scripts/wireguard_setup_rpi.sh 10.8.0.2/24

# En el VPS
sudo bash scripts/wireguard_add_peer_vps.sh 10.8.0.2 PUBLIC_KEY_DE_LA_RPI nombre-rpi
```

### Integracion con el dashboard

- Para instalaciones nuevas, podés sembrar los dispositivos por defecto con `DEFAULT_DEVICES_JSON` en `.env`.
- Para instalaciones existentes, podés migrar hosts locales a IPs VPN con:

```bash
python scripts/switch_devices_to_vpn.py --db data/vidloop_dash.db --mapping-file scripts/devices_vpn.example.json --dry-run
python scripts/switch_devices_to_vpn.py --db data/vidloop_dash.db --mapping-file scripts/devices_vpn.example.json
```

- El archivo de ejemplo está en `scripts/devices_vpn.example.json` y acepta formato simple `nombre -> ip`:

```json
{
	"Principal": "10.50.0.10",
	"Entrada": "10.50.0.11",
	"Fondo": "10.50.0.12"
}
```

- También podés usar un formato extendido con `host`, `port` y `user` si necesitás cambiar algo más que la IP.

## Notas
- El video se genera en el servidor con FFmpeg a partir de imagenes.
- Luego se sube por SSH a cada Raspberry.
- Ajusta `remote_path` y `remote_filename` segun tu configuracion de VIDLOOP.