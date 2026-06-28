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

# Strips a trailing unit suffix like " kB", " MB", " GB" so _safe_int can work.
_UNIT_RE = re.compile(r"\s*[kmgKMG]?[bB]$")


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
    mem_used: int = 0
    mem_free: int = 0
    mem_total: int = 0
    wl_ssid: str = ""
    wl_channel: str = ""
    wl_radio: str = ""
    wl_rate: str = ""
    wl_clients: list[dict[str, str]] = field(default_factory=list)
    lan_ipaddr: str = ""
    dhcp_leases: list[dict[str, str]] = field(default_factory=list)


class DDWRTClient:
    """Async client for DD-WRT routers.

    Must be used as an async context manager or have close() awaited:

        async with DDWRTClient(...) as client:
            data = await client.async_get_data()
    """

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
        self._session: aiohttp.ClientSession | None = None  # created lazily in async context
        _LOGGER.debug(
            "DD-WRT client configured for %s (ssl=%s, password_length=%d)",
            host, ssl, len(password),
        )

    def _ssl_context(self):
        """Return an SSL context that accepts self-signed router certificates."""
        ctx = _ssl_module.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl_module.CERT_NONE
        return ctx

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Create the session the first time we're inside the event loop."""
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
        headers = {
            "Authorization": self._auth_header,
        }
        # Use a permissive SSL context so self-signed router certs are accepted.
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
                _LOGGER.debug("DD-WRT %s response body (first 500 chars): %r", path, text[:500])
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

        # Warn loudly if parsing yielded nothing — this almost always means the
        # response format didn't match the expected {key::value} pattern (e.g. a
        # login-redirect page or a changed firmware format).
        if not r:
            _LOGGER.warning(
                "DD-WRT: parsed zero keys from Status_Router.live.asp — "
                "raw response (first 500 chars): %r",
                router_raw[:500],
            )
        else:
            _LOGGER.debug("DD-WRT router keys: %s", list(r.keys()))

        if not w:
            _LOGGER.warning(
                "DD-WRT: parsed zero keys from Status_Wireless.live.asp — "
                "raw response (first 500 chars): %r",
                wireless_raw[:500],
            )
        else:
            _LOGGER.debug("DD-WRT wireless keys: %s", list(w.keys()))

        # ── Memory ────────────────────────────────────────────────────────────
        # DD-WRT firmware may include a " kB" unit suffix in memory values, e.g.
        # "14836 kB". _safe_int() strips that suffix before converting to int.
        mem_used = _safe_int(r.get("mem_used", "0"))
        mem_free = _safe_int(r.get("mem_free", "0"))

        # ── Load average ──────────────────────────────────────────────────────
        # Some DD-WRT builds expose a standalone {load_avg::…} key; others embed
        # the load average inside the uptime string:
        #   "12:53:44 up 12:59, load average: 0.03, 0.04, 0.00"
        # We try the dedicated key first, then fall back to extracting from uptime.
        uptime_raw = r.get("uptime", "")
        load_avg_raw = r.get("load_avg", "")
        if not load_avg_raw:
            m = _LOAD_RE.search(uptime_raw)
            if m:
                load_avg_raw = m.group(1).replace(", ", " ")
                _LOGGER.debug("DD-WRT: load_avg extracted from uptime string: %r", load_avg_raw)

        # Strip the load-average trailer from the uptime display string so the
        # Uptime sensor only shows the human-readable uptime portion.
        uptime_display = _LOAD_RE.sub("", uptime_raw).rstrip(", ").strip()

        # ── LAN IP ────────────────────────────────────────────────────────────
        # Different firmware versions use different key names for the LAN address.
        lan_ip = (
            r.get("lan_ipaddr")
            or r.get("lan_ip")
            or r.get("local_ip")
            or r.get("ip_addr")
            or ""
        )

        # ── WAN fields ────────────────────────────────────────────────────────
        # Try common alternate key names used by different firmware builds.
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

        # ── WiFi radio state ──────────────────────────────────────────────────
        # The `wl_radio` key is absent on many builds.  Infer from clients/SSID
        # when the key is missing or empty.
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
            mem_total=mem_used + mem_free,
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
    """Convert a string to int, stripping commas and trailing unit suffixes (e.g. ' kB')."""
    try:
        cleaned = _UNIT_RE.sub("", value).replace(",", "").strip()
        return int(cleaned)
    except (ValueError, AttributeError):
        return 0


def _resolve_radio(wl_radio: str, wl_ssid: str, active_wireless: str) -> str:
    """Return a canonical radio-state string.

    DD-WRT sometimes omits the wl_radio key entirely.  When it is present the
    value can be: "Enabled", "Disabled", "Radio is On", "Radio is Off", "1",
    "0", etc.  When it is absent we infer the state from context:
      - An SSID being broadcast → radio is on
      - Active wireless clients → radio is on (clients wouldn't be associated otherwise)
    """
    if wl_radio:
        lower = wl_radio.lower()
        if "on" in lower or lower in ("1", "true", "enabled"):
            return "Enabled"
        if "off" in lower or lower in ("0", "false", "disabled"):
            return "Disabled"
        # Unknown value — return as-is so the binary sensor can still try.
        return wl_radio

    # Key absent — infer from context.
    if wl_ssid.strip() or active_wireless.strip():
        _LOGGER.debug(
            "DD-WRT: wl_radio key absent; inferring radio ON from ssid=%r clients=%r",
            wl_ssid, active_wireless[:80],
        )
        return "Enabled"

    return ""


def _parse_clients(raw: str) -> list[dict[str, str]]:
    """Parse the active_wireless CSV blob.

    DD-WRT packs each client as 9 comma-separated fields:
      MAC, interface, uptime, tx_rate, rx_rate, signal, noise, snr, quality

    Fields are single-quoted, e.g.:
      'AA:BB:CC:DD:EE:FF','ath0','0 days 00:01:23','130','130','-55','-95','40','100'
    """
    if not raw:
        return []
    fields = [f.strip().strip("'") for f in raw.split(",")]
    clients: list[dict[str, str]] = []
    # Step by 9; stop when fewer than 9 fields remain to avoid index errors.
    for i in range(0, len(fields) - 8, 9):
        c = fields[i : i + 9]
        clients.append({
            "mac": c[0], "interface": c[1], "uptime": c[2],
            "tx_rate": c[3], "rx_rate": c[4], "signal": c[5],
            "noise": c[6], "snr": c[7], "quality": c[8],
        })
    return clients


def _parse_dhcp(raw: str) -> list[dict[str, str]]:
    """Parse the dhcp_leases CSV blob.

    DD-WRT packs each lease as 5 comma-separated fields:
      hostname, MAC, IP, expires, type

    The legacy code used 4 fields (missing 'type'), which shifted every
    second lease by one position and produced zero leases after the first.
    """
    if not raw:
        return []
    fields = [f.strip().strip("'") for f in raw.split(",")]
    leases: list[dict[str, str]] = []

    # Try 5-field format first (current DD-WRT default).
    # Fall back to 4-field if the count is not a multiple of 5 but is of 4.
    stride = 5 if len(fields) % 5 == 0 else 4
    _LOGGER.debug("DD-WRT DHCP: %d fields → stride=%d", len(fields), stride)

    for i in range(0, len(fields) - (stride - 1), stride):
        c = fields[i : i + stride]
        lease: dict[str, str] = {
            "hostname": c[0],
            "mac": c[1],
            "ip": c[2],
            "expires": c[3],
        }
        if stride == 5:
            lease["type"] = c[4]
        leases.append(lease)
    return leases
