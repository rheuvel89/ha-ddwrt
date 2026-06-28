# DD-WRT Custom Home Assistant Integration

A local-polling integration that pulls live data from your DD-WRT router and
exposes it as sensors, binary sensors, and device trackers in Home Assistant.

## Features

| Platform | Entities |
|---|---|
| **Sensor** | WAN IP, WAN status, WAN protocol, uptime, load average, memory used/free/%, WiFi SSID, channel, TX rate, WiFi client count, DHCP lease count, LAN IP |
| **Binary Sensor** | WAN connected, WiFi radio on/off |
| **Device Tracker** | One entity per wireless client (auto-discovered), with signal/SNR/rate attributes |

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
3. Fill in:
   - **IP Address / Hostname** — e.g. `192.168.1.1`
   - **Port** — default `80` (use `443` with HTTPS)
   - **Username** — default `root`
   - **Password** — your router web UI password
   - **Use HTTPS** — toggle if your router uses SSL

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
| `sensor.ddwrt_wan_ip_address` | Current WAN/public IP |
| `sensor.ddwrt_wan_status` | `Connected` / `Disconnected` |
| `sensor.ddwrt_wan_protocol` | DHCP, PPPoE, Static, etc. |
| `sensor.ddwrt_uptime` | Human-readable router uptime |
| `sensor.ddwrt_load_average` | 1/5/15-min load averages |
| `sensor.ddwrt_memory_used` | RAM used (KB) |
| `sensor.ddwrt_memory_free` | RAM free (KB) |
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

### Device Trackers
One `device_tracker.*` entity is created per wireless client. They are named
by MAC address initially — rename them via the HA UI or `known_devices.yaml`.

Extra attributes per tracker: `signal`, `noise`, `snr`, `tx_rate`, `rx_rate`,
`interface`, `uptime`.

---

## Update Interval

Default polling interval is **30 seconds**. To change it, override the constant
in `const.py` (`DEFAULT_SCAN_INTERVAL`).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `cannot_connect` error | Check IP/port, ensure web UI is reachable from HA host |
| All sensors `unavailable` | Check router credentials; DD-WRT uses HTTP Basic Auth |
| No device trackers | Confirm clients are associated; check `/Status_Wireless.live.asp` manually |
| Wrong memory values | Some DD-WRT builds report KB, others MB — check your build's output |
