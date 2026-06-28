# DD-WRT Custom Home Assistant Integration

A local-polling integration that pulls live data from your DD-WRT router and
exposes it as sensors, binary sensors, and device trackers in Home Assistant.

> **Tested on:** DD-WRT v3.0-r55723 std (04/09/24)
> Other builds may work but have not been verified.

## Features

| Platform | Entities |
|---|---|
| **Sensor** | WAN IP, WAN protocol, uptime, load average, memory used/free/%, WiFi SSID, channel, TX rate, WiFi client count, DHCP lease count, LAN IP |
| **Binary Sensor** | WAN connected, WiFi radio on/off |
| **Device Tracker** | One entity per wireless client (`[ddwrt-wifi]`) and one per DHCP lease (`[ddwrt-dhcp]`), with signal/SNR/rate/IP/hostname attributes |

---

## Requirements

- Home Assistant 2023.1 or later
- A DD-WRT router with the web interface accessible from the HA host
- A user account on the router (default: `root`)

---

## Installation

### HACS (recommended)
1. Add this repository as a custom HACS repository.
2. Search for **DD-WRT** and install.
3. Restart Home Assistant.

### Manual
1. Copy the `custom_components/ddwrt/` folder into your HA config directory:
   ```
   <config>/custom_components/ddwrt/
   ```
2. Restart Home Assistant.

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **DD-WRT Router**.
3. **Step 1 — Connection:**
   - **IP Address / Hostname** — e.g. `192.168.1.1`
   - **Port** — default `80` (use `443` with HTTPS)
   - **Username** — default `root`
   - **Password** — your router web UI password
   - **Use HTTPS** — toggle if your router uses SSL
4. **Step 2 — Device Trackers:**
   - **Track WiFi clients** — creates a tracker per currently associated wireless device
   - **Track DHCP clients** — creates a tracker per active DHCP lease (wired + wireless)

You can change the tracker options later via **Settings → Devices & Services → DD-WRT → Configure**.

---

## DD-WRT Router Setup

Ensure the following are enabled on your router:

- **Services → Management**: Enable web interface (`HTTP` or `HTTPS`)
- **Administration → Management**: Enable remote access if HA is not on the LAN
- The polling user needs read access to the status pages — the default `root`
  account works out of the box.

---

## Entities Reference

### Sensors
| Entity | Description |
|---|---|
| `sensor.ddwrt_wan_ip_address` | Current WAN IP |
| `sensor.ddwrt_wan_protocol` | DHCP, PPPoE, Static, etc. |
| `sensor.ddwrt_uptime` | Human-readable router uptime |
| `sensor.ddwrt_load_average` | 1/5/15-min load averages |
| `sensor.ddwrt_memory_used` | RAM used (kB) |
| `sensor.ddwrt_memory_free` | RAM free (kB) |
| `sensor.ddwrt_memory_usage` | RAM usage % |
| `sensor.ddwrt_wifi_ssid` | Primary SSID |
| `sensor.ddwrt_wifi_channel` | Active channel |
| `sensor.ddwrt_wifi_tx_rate` | Transmit rate |
| `sensor.ddwrt_wifi_clients` | Count of associated WiFi clients |
| `sensor.ddwrt_dhcp_leases` | Count of active DHCP leases |
| `sensor.ddwrt_lan_ip_address` | LAN gateway IP |

### Binary Sensors
| Entity | Description |
|---|---|
| `binary_sensor.ddwrt_wan_connected` | `on` when WAN is up |
| `binary_sensor.ddwrt_wifi_radio` | `on` when radio is enabled |

> **Note on WAN Connected vs static WAN:** DD-WRT reports a status of `Error`
> for static WAN connections because there is no dynamic connection handshake.
> The `WAN Connected` binary sensor correctly reports `on` based on WAN IP
> presence, not the status string.

### Device Trackers

Two tracker families are created, toggled independently during setup:

**WiFi trackers** (`[ddwrt-wifi] AA:BB:CC:DD:EE:FF`)
- One entity per device currently associated with the wireless radio
- `connected` while the device appears in the active client list
- Attributes: `signal`, `noise`, `snr`, `tx_rate`, `rx_rate`, `interface`, `uptime`

**DHCP trackers** (`[ddwrt-dhcp] hostname`)
- One entity per active DHCP lease (covers wired and wireless clients)
- `connected` while the lease exists in the router's lease table
- Attributes: `ip`, `hostname`, `expires`

Trackers appear under **Developer Tools → States** filtered by `device_tracker.`.
You can assign them to people under **Settings → People**.

---

## Update Interval

Default polling interval is **30 seconds**. To change it, override
`DEFAULT_SCAN_INTERVAL` in `const.py`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `cannot_connect` error | Check IP/port, ensure web UI is reachable from HA host |
| All sensors `unavailable` | Check credentials; DD-WRT uses HTTP Basic Auth |
| Device trackers not showing | Look in **Developer Tools → States**, filter `device_tracker.` — they don't appear on the integration device page |
| Wrong memory values | Some DD-WRT builds report KB, others MB — check your build's live.asp output |
| Untested firmware | This integration was developed against DD-WRT v3.0-r55723 std (04/09/24); other builds may expose different key names |
