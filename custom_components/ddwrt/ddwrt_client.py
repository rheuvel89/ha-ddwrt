"""DD-WRT router client — parses the live status pages."""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field

import aiohttp

_LOGGER = logging.getLogger(__name__)

# DD-WRT exposes data as a series of {key::value} pairs in its .live.asp pages.
_KV_RE = re.compile(r"\{(\w+)::([^}]*)\}")


def _parse_live(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in _KV_RE.finditer(text)}


def _basic_auth_header(username: str, password: str) -> str:
    """Build a raw Basic Auth header value.

    Avoids aiohttp's shared-session auth which can be unreliable on some
    DD-WRT builds that are strict about the Authorization header format.
    """
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


@dataclass
class DDWRTData:
    """All data pulled from the router."""

    # Router / WAN
    router_name: str = ""
    wan_ipaddr: str = ""
    wan_status: str = ""
    wan_proto: str = ""
    uptime: str = ""
    load_avg: str = ""
    mem_used: int = 0
    mem_free: int = 0
    mem_total: int = 0

    # Wireless
    wl_ssid: str = ""
    wl_channel: str = ""
    wl_radio: str = ""
    wl_rate: str = ""
    wl_clients: list[dict[str, str]] = field(default_factory=list)

    # LAN
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
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._port = port
        self._ssl = ssl
        self._base = f"{'https' if ssl else 'http'}://{host}:{port}"
        self._auth_header = _basic_auth_header(username, password)

        # Always create a dedicated session so we never share cookies/state
        # with the HA-wide client session, which can cause 401s on DD-WRT.
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self._ssl)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _fetch(self, path: str) -> str:
        session = await self._get_session()
        url = f"{self._base}{path}"
        headers = {
            "Authorization": self._auth_header,
            # Some DD-WRT builds check the Referer header before serving pages
            "Referer": self._base + "/",
        }
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                _LOGGER.debug("DD-WRT %s → HTTP %s", url, resp.status)
                if resp.status == 401:
                    raise AuthError(f"Authentication failed for {url} (401)")
                resp.raise_for_status()
                return await resp.text()
        except AuthError:
            raise
        except aiohttp.ClientResponseError as err:
            _LOGGER.error("DD-WRT HTTP error fetching %s: %s %s", url, err.status, err.message)
            raise ConnectionError(f"HTTP {err.status} from {url}") from err
        except aiohttp.ServerTimeoutError as err:
            _LOGGER.error("DD-WRT timed out fetching %s", url)
            raise ConnectionError(f"Timeout reaching {url}") from err
        except aiohttp.ClientConnectorError as err:
            _LOGGER.error("DD-WRT connection refused or DNS failure for %s: %s", url, err)
            raise ConnectionError(f"Cannot connect to {url}: {err}") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("DD-WRT unexpected aiohttp error for %s: %s (%s)", url, err, type(err).__name__)
            raise ConnectionError(f"Cannot reach router at {url}: {err}") from err
        except Exception as err:
            _LOGGER.error("DD-WRT unexpected error for %s: %s (%s)", url, err, type(err).__name__)
            raise ConnectionError(f"Unexpected error reaching {url}: {err}") from err

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def test_connection(self) -> bool:
        """Return True if the router is reachable and credentials work."""
        try:
            await self._fetch("/Status_Router.live.asp")
            return True
        except AuthError:
            _LOGGER.error(
                "DD-WRT authentication failed — check username and password"
            )
            return False
        except ConnectionError as err:
            _LOGGER.error("DD-WRT connection error: %s", err)
            return False

    async def async_get_data(self) -> DDWRTData:
        """Fetch and return all router data."""
        router_raw = await self._fetch("/Status_Router.live.asp")
        wireless_raw = await self._fetch("/Status_Wireless.live.asp")

        r = _parse_live(router_raw)
        w = _parse_live(wireless_raw)

        _LOGGER.debug("DD-WRT router keys: %s", list(r.keys()))
        _LOGGER.debug("DD-WRT wireless keys: %s", list(w.keys()))

        mem_used = _safe_int(r.get("mem_used", "0"))
        mem_free = _safe_int(r.get("mem_free", "0"))
        mem_total = mem_used + mem_free

        return DDWRTData(
            router_name=r.get("router_name", "DD-WRT"),
            wan_ipaddr=r.get("wan_ipaddr", ""),
            wan_status=r.get("wan_status", ""),
            wan_proto=r.get("wan_proto", ""),
            uptime=r.get("uptime", ""),
            load_avg=r.get("load_avg", ""),
            mem_used=mem_used,
            mem_free=mem_free,
            mem_total=mem_total,
            lan_ipaddr=r.get("lan_ipaddr", ""),
            wl_ssid=w.get("wl_ssid", ""),
            wl_channel=w.get("wl_channel", ""),
            wl_radio=w.get("wl_radio", ""),
            wl_rate=w.get("wl_rate", ""),
            wl_clients=_parse_clients(w.get("active_wireless", "")),
            dhcp_leases=_parse_dhcp(r.get("dhcp_leases", "")),
        )


class AuthError(ConnectionError):
    """Raised specifically on 401 so callers can surface a clearer error."""


# ------------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------------

def _safe_int(value: str) -> int:
    try:
        return int(value.replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0


def _parse_clients(raw: str) -> list[dict[str, str]]:
    """
    DD-WRT encodes wireless clients as a flat comma-separated list:
    MAC,interface,uptime,tx,rx,signal,noise,snr,quality, …
    grouped in chunks of 9 fields.
    """
    if not raw:
        return []
    fields = [f.strip().strip("'") for f in raw.split(",")]
    clients: list[dict[str, str]] = []
    chunk = 9
    for i in range(0, len(fields) - chunk + 1, chunk):
        c = fields[i : i + chunk]
        clients.append(
            {
                "mac": c[0],
                "interface": c[1],
                "uptime": c[2],
                "tx_rate": c[3],
                "rx_rate": c[4],
                "signal": c[5],
                "noise": c[6],
                "snr": c[7],
                "quality": c[8],
            }
        )
    return clients


def _parse_dhcp(raw: str) -> list[dict[str, str]]:
    """
    DHCP leases: hostname,mac,ip,expires  repeated, comma-separated.
    """
    if not raw:
        return []
    fields = [f.strip().strip("'") for f in raw.split(",")]
    leases: list[dict[str, str]] = []
    chunk = 4
    for i in range(0, len(fields) - chunk + 1, chunk):
        c = fields[i : i + chunk]
        leases.append(
            {
                "hostname": c[0],
                "mac": c[1],
                "ip": c[2],
                "expires": c[3],
            }
        )
    return leases
