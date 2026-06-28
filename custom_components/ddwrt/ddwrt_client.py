"""DD-WRT router client — parses the live status pages."""
from __future__ import annotations

import base64
import logging
import re
import ssl as _ssl_module
from dataclasses import dataclass, field

import aiohttp

_LOGGER = logging.getLogger(__name__)

_KV_RE = re.compile(r"\{(\w+)::([^}]*)\}")

# Matches the Linux uptime load-average trailer:
#   "... load average: 0.03, 0.04, 0.00"
_LOAD_RE = re.compile(r"load average:\s*([\d.]+(?:,\s*[\d.]+)*)", re.IGNORECASE)

# Extracts a bare IP address from strings like "&nbsp;IP: 192.168.0.2"
_IP_RE = re.compile(r'(\d{1,3}(?:\.\d{1,3}){3})')

# MAC address pattern used to anchor per-client records in the wireless blob
_MAC_RE = re.compile(r'^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$')


def _parse_live(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in _KV_RE.finditer(text)}


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


@dataclass
class DDWRTData:
    router_name: str = ""
    wan_ipaddr: str = ""
    wan_status: str = ""
    wan_proto: str = ""
    uptime: str = ""
    load_avg: str = ""
    mem_used: int | None = None
    mem_free: int | None = None
    mem_total: int | None = None
    wl_ssid: str = ""
    wl_channel: str = ""
    wl_radio: str = ""
    wl_rate: str = ""
    wl_clients: list[dict[str, str]] = field(default_factory=list)
    lan_ipaddr: str = ""
    dhcp_leases: list[dict[str, str]] = field(default_factory=list)


class DDWRTClient:
    """Async client for DD-WRT routers."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 80,
        ssl: bool = False,
    ) -> None:
        self._base = f"{'https' if ssl else 'http'}://{host}:{port}"
        self._use_ssl = ssl
        self._auth_header = _basic_auth_header(username, password)
        self._session: aiohttp.ClientSession | None = None
        _LOGGER.debug(
            "DD-WRT client configured for %s (ssl=%s, password_length=%d)",
            host, ssl, len(password),
        )

    def _ssl_context(self):
        ctx = _ssl_module.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl_module.CERT_NONE
        return ctx

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> DDWRTClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _fetch(self, path: str) -> str:
        session = await self._ensure_session()
        url = f"{self._base}{path}"
        headers = {"Authorization": self._auth_header}
        ssl_param = self._ssl_context() if self._use_ssl else False
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
                ssl=ssl_param,
            ) as resp:
                _LOGGER.debug("DD-WRT %s → HTTP %s", url, resp.status)
                if resp.status == 401:
                    raise AuthError(f"Authentication failed (401) for {url}")
                if resp.status == 400:
                    body = await resp.text()
                    _LOGGER.error("DD-WRT 400 response body: %r", body[:500])
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history, status=400, message="BAD REQUEST"
                    )
                resp.raise_for_status()
                text = await resp.text()
                _LOGGER.debug("DD-WRT %s response (first 200 chars): %r", path, text[:200])
                return text
        except AuthError:
            raise
        except aiohttp.ClientResponseError as err:
            _LOGGER.error("DD-WRT HTTP error %s %s: %s", err.status, url, err.message)
            raise ConnectionError(f"HTTP {err.status} from {url}") from err
        except aiohttp.ServerTimeoutError:
            _LOGGER.error("DD-WRT timeout fetching %s", url)
            raise ConnectionError(f"Timeout reaching {url}")
        except aiohttp.ClientConnectorError as err:
            _LOGGER.error("DD-WRT cannot connect to %s: %s", url, err)
            raise ConnectionError(f"Cannot connect to {url}: {err}") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("DD-WRT aiohttp error for %s: %s (%s)", url, err, type(err).__name__)
            raise ConnectionError(f"aiohttp error reaching {url}: {err}") from err

    async def async_get_data(self) -> DDWRTData:
        router_raw = await self._fetch("/Status_Router.live.asp")
        wireless_raw = await self._fetch("/Status_Wireless.live.asp")

        r = _parse_live(router_raw)
        w = _parse_live(wireless_raw)

        if not r:
            _LOGGER.warning(
                "DD-WRT: parsed zero keys from Status_Router.live.asp — "
                "raw response (first 500 chars): %r", router_raw[:500],
            )
        if not w:
            _LOGGER.warning(
                "DD-WRT: parsed zero keys from Status_Wireless.live.asp — "
                "raw response (first 500 chars): %r", wireless_raw[:500],
            )

        # ── Memory ────────────────────────────────────────────────────────────
        # This firmware build packs /proc/meminfo into a single `mem_info` CSV
        # blob rather than exposing separate mem_used/mem_free keys.
        # Format: ...'MemTotal:','470320','kB','MemFree:','374720','kB',...
        mem_total, mem_free, mem_used = _parse_mem_info(r.get("mem_info", ""))

        # Fall back to standalone keys for firmware builds that use them.
        if mem_total is None:
            _free = _safe_int(r.get("mem_free", "")) or None
            _used = _safe_int(r.get("mem_used", "")) or None
            if _free is not None or _used is not None:
                mem_free = _free or 0
                mem_used = _used or 0
                mem_total = (mem_free or 0) + (mem_used or 0) or None

        # ── Load average ──────────────────────────────────────────────────────
        uptime_raw = r.get("uptime", "") or w.get("uptime", "")
        load_avg_raw = r.get("load_avg", "")
        if not load_avg_raw:
            m = _LOAD_RE.search(uptime_raw)
            if m:
                load_avg_raw = m.group(1).replace(", ", " ")

        # Strip the load-average trailer from the uptime display string.
        uptime_display = _LOAD_RE.sub("", uptime_raw).rstrip(", ").strip()

        # ── LAN IP ────────────────────────────────────────────────────────────
        # This firmware exposes the IP via `ipinfo` as "&nbsp;IP: 192.168.0.2"
        # rather than a dedicated lan_ipaddr key.
        lan_ip = (
            r.get("lan_ipaddr")
            or r.get("lan_ip")
            or r.get("local_ip")
            or ""
        )
        if not lan_ip:
            ipinfo = r.get("ipinfo", "") or w.get("ipinfo", "")
            m2 = _IP_RE.search(ipinfo)
            if m2:
                lan_ip = m2.group(1)

        # ── WAN fields ────────────────────────────────────────────────────────
        wan_ip = (
            r.get("wan_ipaddr")
            or r.get("wan_ip")
            or r.get("wanip")
            or ""
        )
        wan_status = (
            r.get("wan_status")
            or r.get("wan_3g_status")
            or r.get("wan_connected")
            or ""
        )
        wan_proto = (
            r.get("wan_proto")
            or r.get("wan_type")
            or ""
        )

        # ── WiFi radio ────────────────────────────────────────────────────────
        # This firmware returns wl_radio = "Active" (not "Enabled"/"on").
        wl_radio_raw = w.get("wl_radio", "")
        wl_clients_raw = w.get("active_wireless", "")
        wl_ssid = w.get("wl_ssid", "")
        wl_radio = _resolve_radio(wl_radio_raw, wl_ssid, wl_clients_raw)

        return DDWRTData(
            router_name=r.get("router_name", "DD-WRT"),
            wan_ipaddr=wan_ip,
            wan_status=wan_status,
            wan_proto=wan_proto,
            uptime=uptime_display,
            load_avg=load_avg_raw,
            mem_used=mem_used,
            mem_free=mem_free,
            mem_total=mem_total,
            lan_ipaddr=lan_ip,
            wl_ssid=wl_ssid,
            wl_channel=w.get("wl_channel", ""),
            wl_radio=wl_radio,
            wl_rate=w.get("wl_rate", ""),
            wl_clients=_parse_clients(wl_clients_raw),
            dhcp_leases=_parse_dhcp(r.get("dhcp_leases", "")),
        )


class AuthError(ConnectionError):
    """Raised on HTTP 401."""


def _safe_int(value: str) -> int:
    """Convert a string to int, stripping commas and trailing unit suffixes."""
    try:
        # Strip trailing unit suffix e.g. " kB", " MB"
        cleaned = re.sub(r"\s*[kmgKMG]?[bB]$", "", value).replace(",", "").strip()
        return int(cleaned)
    except (ValueError, AttributeError):
        return 0


def _parse_mem_info(blob: str) -> tuple[int | None, int | None, int | None]:
    """Parse the mem_info CSV blob from /proc/meminfo.

    Returns (total_kB, free_kB, used_kB) or (None, None, None) if not parseable.

    The blob looks like:
      ,'total:','used:','free:',...,'MemTotal:','470320','kB','MemFree:','374720','kB',...
    """
    if not blob:
        return None, None, None

    fields = [f.strip().strip("'") for f in blob.split(",")]

    def _find(label: str) -> int | None:
        for i, f in enumerate(fields):
            if f == label and i + 1 < len(fields):
                try:
                    return int(fields[i + 1])
                except ValueError:
                    return None
        return None

    total = _find("MemTotal:")
    free = _find("MemFree:")
    if total is None or free is None:
        return None, None, None

    # Available memory (free + reclaimable) gives a more useful "free" figure.
    available = _find("MemAvailable:")
    effective_free = available if available is not None else free
    used = total - effective_free

    return total, effective_free, used


def _resolve_radio(wl_radio: str, wl_ssid: str, active_wireless: str) -> str:
    """Return 'Enabled' or 'Disabled' from any firmware radio value.

    Known values: 'Active', 'Enabled', 'Disabled', 'Radio is On/Off', '1', '0'.
    When the key is absent, infer from SSID broadcast / client presence.
    """
    if wl_radio:
        lower = wl_radio.lower()
        # "Active" is this firmware's "on" value
        if lower in ("active", "enabled", "1", "true") or "on" in lower:
            return "Enabled"
        if lower in ("inactive", "disabled", "0", "false") or "off" in lower:
            return "Disabled"
        # Unknown string — return as-is
        return wl_radio

    # Key absent — infer from context.
    if wl_ssid.strip() or active_wireless.strip():
        _LOGGER.debug("DD-WRT: wl_radio absent; inferring ON from ssid/clients")
        return "Enabled"
    return ""


def _parse_clients(raw: str) -> list[dict[str, str]]:
    """Parse the active_wireless CSV blob.

    This firmware uses 17 fields per client, MAC-anchored:
      [0]  MAC
      [1]  '' (padding)
      [2]  interface (wlan0/wlan1)
      [3]  uptime
      [4]  tx_rate
      [5]  rx_rate
      [6]  mode (VHT80SGI etc.)
      [7]  signal (dBm)
      [8]  noise (dBm)
      [9]  snr
      [10] quality
      [11..14] per-antenna RSSI values
      [15] '' (padding)
      [16] interface (repeated, serves as prefix for next record)

    We locate records by MAC address position rather than fixed stride so
    the parser is robust against blob variations between firmware builds.
    """
    if not raw:
        return []

    fields = [f.strip().strip("'") for f in raw.split(",")]
    clients: list[dict[str, str]] = []

    for i, f in enumerate(fields):
        if not _MAC_RE.match(f):
            continue
        # Need at least 15 fields from MAC position
        if i + 14 >= len(fields):
            continue
        c = fields[i:i + 15]
        clients.append({
            "mac":       c[0],
            "interface": c[2],
            "uptime":    c[3],
            "tx_rate":   c[4],
            "rx_rate":   c[5],
            "mode":      c[6],
            "signal":    c[7],
            "noise":     c[8],
            "snr":       c[9],
            "quality":   c[10],
            "rssi0":     c[11],
            "rssi1":     c[12],
            "rssi2":     c[13],
            "rssi3":     c[14],
        })

    return clients


def _parse_dhcp(raw: str) -> list[dict[str, str]]:
    """Parse the dhcp_leases CSV blob.

    DD-WRT typically uses 5 fields per lease (hostname, MAC, IP, expires, type).
    Falls back to 4-field format for older builds.
    When the key is absent entirely (as on some builds), returns [].
    """
    if not raw:
        return []

    fields = [f.strip().strip("'") for f in raw.split(",")]
    leases: list[dict[str, str]] = []

    # Auto-detect stride: prefer 5, fall back to 4.
    if len(fields) % 5 == 0:
        stride = 5
    elif len(fields) % 4 == 0:
        stride = 4
    else:
        # Non-divisible — try 5 anyway (truncates last partial record gracefully)
        stride = 5

    _LOGGER.debug("DD-WRT DHCP: %d fields → stride=%d", len(fields), stride)

    for i in range(0, len(fields) - (stride - 1), stride):
        c = fields[i:i + stride]
        lease: dict[str, str] = {
            "hostname": c[0],
            "mac":      c[1],
            "ip":       c[2],
            "expires":  c[3],
        }
        if stride == 5:
            lease["type"] = c[4]
        leases.append(lease)

    return leases
