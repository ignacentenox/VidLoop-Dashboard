# VIDLOOP - Guia Tecnica Interna

Este archivo concentra la documentacion operativa detallada de los scripts auxiliares de WireGuard incluidos en el repo.

No esta enlazado desde el README principal para mantener la documentacion publica mas corta.

## Topologia asumida por los scripts

- VPS WireGuard: `10.8.0.1`
- Red VPN: `10.8.0.0/24`
- Endpoint publico del servidor: `82.25.77.55:51820`
- Interfaz del servidor: `wg0`

## Scripts disponibles

```bash
scripts/wireguard_setup_rpi.sh
scripts/wireguard_add_peer_vps.sh
scripts/wireguard_generate_client_conf.sh
scripts/switch_devices_to_vpn.py
```

## Flujo recomendado

### Opcion A: alta automatica desde la Raspberry

Usar esta opcion cuando tenes acceso shell a la Raspberry y queres que el equipo se configure solo.

Requisito:

- ejecutar el script desde una copia del repo en la Raspberry, o copiar el archivo al host antes de correrlo

Paso 1. En la Raspberry, ejecutar como root:

```bash
sudo bash scripts/wireguard_setup_rpi.sh 10.8.0.2/24
```

Eso hace lo siguiente:

- instala WireGuard si no existe
- genera `/etc/wireguard/private.key`
- genera `/etc/wireguard/public.key`
- crea `/etc/wireguard/wg0.conf`
- habilita y arranca `wg-quick@wg0`
- muestra la public key final del cliente

Paso 2. Copiar la public key mostrada por la Raspberry.

Paso 3. En el VPS, agregar el peer:

```bash
sudo bash scripts/wireguard_add_peer_vps.sh 10.8.0.2 PUBLIC_KEY_DE_LA_RPI nombre-rpi
```

Ejemplo:

```bash
sudo bash scripts/wireguard_add_peer_vps.sh 10.8.0.2 7FKzip5CE8UD65gcPZ0PCdVy5RtsiI3jmcNM+dxBGW0= rpi-cocina
```

Paso 4. Verificar:

```bash
sudo wg show
ping -c 3 10.8.0.1
```

### Opcion B: generar configuracion manual del cliente

Usar esta opcion cuando queres armar el `wg0.conf` antes de tocar el dispositivo o cuando el cliente no va a generar su propia configuracion.

Paso 1. Generar claves del cliente:

```bash
wg genkey | tee client.private | wg pubkey > client.public
```

Paso 2. En el VPS, generar el archivo del cliente:

```bash
sudo bash scripts/wireguard_generate_client_conf.sh 10.8.0.3/24 "$(cat client.private)" /root/rpi-3-wg0.conf
```

Paso 3. Copiar el archivo al cliente como `/etc/wireguard/wg0.conf`.

Paso 4. En el cliente, habilitar la interfaz:

```bash
sudo systemctl enable --now wg-quick@wg0
```

Paso 5. En el VPS, agregar el peer:

```bash
sudo bash scripts/wireguard_add_peer_vps.sh 10.8.0.3 "$(cat client.public)" rpi-3
```

## Detalle tecnico por script

### `wireguard_setup_rpi.sh`

Uso:

```bash
sudo bash scripts/wireguard_setup_rpi.sh [vpn_cidr] [server_public_key] [server_endpoint] [allowed_ips] [dns]
```

Defaults actuales:

- `vpn_cidr`: `10.8.0.2/24`
- `server_public_key`: valor hardcodeado en el script
- `server_endpoint`: `82.25.77.55:51820`
- `allowed_ips`: `10.8.0.0/24`
- `dns`: `1.1.1.1`

Comportamiento:

- exige root
- instala paquetes con `apt`
- en Debian 10 agrega repositorio archive para poder instalar WireGuard
- genera claves si no existen
- preserva `private.key` existente
- si falta `public.key`, la recalcula desde la privada
- escribe `/etc/wireguard/wg0.conf`
- hace `systemctl enable --now wg-quick@wg0`
- imprime estado con `wg show`
- prueba conectividad con `ping -c 3 10.8.0.1`

Ejemplo completo:

```bash
sudo bash scripts/wireguard_setup_rpi.sh 10.8.0.12/24 APUBLICKEYDELVPS= 82.25.77.55:51820 10.8.0.0/24 1.1.1.1
```

### `wireguard_add_peer_vps.sh`

Uso:

```bash
sudo bash scripts/wireguard_add_peer_vps.sh <ip_vpn> <public_key> [nombre_peer]
```

Ejemplo:

```bash
sudo bash scripts/wireguard_add_peer_vps.sh 10.8.0.2 7FKzip5CE8UD65gcPZ0PCdVy5RtsiI3jmcNM+dxBGW0= rpi-cocina
```

Comportamiento:

- exige root
- modifica `/etc/wireguard/wg0.conf`
- valida que la IP tenga formato `10.8.0.X`
- evita duplicar la misma `PublicKey`
- evita reutilizar una `AllowedIPs = X/32` ya ocupada
- reinicia `wg-quick@wg0`
- ejecuta `wg show` al final

Notas:

- este script se corre en el VPS, no en la Raspberry
- la IP se pasa sin mascara, por ejemplo `10.8.0.2`

### `wireguard_generate_client_conf.sh`

Uso:

```bash
sudo bash scripts/wireguard_generate_client_conf.sh <ip_vpn_cidr> <client_private_key> <output_path> [allowed_ips] [dns]
```

Ejemplo:

```bash
sudo bash scripts/wireguard_generate_client_conf.sh 10.8.0.20/24 "$(cat client.private)" /root/rpi-20-wg0.conf 10.8.0.0/24 1.1.1.1
```

Comportamiento:

- exige root
- requiere que exista `/etc/wireguard/server_public.key`
- no genera claves del cliente
- escribe el archivo final en `output_path`
- aplica permisos `600`

Salida esperada:

```ini
[Interface]
Address = 10.8.0.20/24
PrivateKey = ...
DNS = 1.1.1.1

[Peer]
PublicKey = ...
Endpoint = 82.25.77.55:51820
AllowedIPs = 10.8.0.0/24
PersistentKeepalive = 25
```

## Integracion con el dashboard

Una vez que las Raspberry responden por VPN, el dashboard puede pasar a usar esas IPs.

### Migracion de dispositivos existentes

Primero correr en modo simulacion:

```bash
python scripts/switch_devices_to_vpn.py --db data/vidloop_dash.db --mapping-file scripts/devices_vpn.example.json --dry-run
```

Si el resultado es correcto, aplicar:

```bash
python scripts/switch_devices_to_vpn.py --db data/vidloop_dash.db --mapping-file scripts/devices_vpn.example.json
```

Formato simple del mapping:

```json
{
  "Principal": "10.50.0.10",
  "Entrada": "10.50.0.11",
  "Fondo": "10.50.0.12"
}
```

Formato extendido permitido:

```json
[
  {
    "name": "Principal",
    "host": "10.50.0.10",
    "port": 22,
    "user": "pi"
  }
]
```

Comportamiento del migrador:

- busca dispositivos habilitados en la tabla `devices`
- cruza por `name`
- permite actualizar `host`, `port` y `user`
- en `--dry-run` solo muestra diferencias
- sin `--dry-run` escribe en la base SQLite

## Riesgos y observaciones

- Hay valores hardcodeados de red, endpoint y public key del servidor. Si cambia la infraestructura, hay que ajustar los scripts.
- `wireguard_generate_client_conf.sh` depende de que el VPS ya tenga exportada su public key en `/etc/wireguard/server_public.key`.
- El README principal no referencia este archivo a proposito, pero sigue siendo visible para quien navegue el repositorio completo.