"""DD-WRT router client — parses the live status pages."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# DD-WRT exposes data as a series of {key::value} pairs in its .live.asp pages.
_KV_RE = re.compile(r"\{(\w+)::([^}]*)\}")


def _parse_live(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in _KV_RE.finditer(text)}


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
        self._session = session
        self._own_session = session is None
        scheme = "https" if ssl else "http"
        self._base = f"{scheme}://{host}:{port}"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            auth = aiohttp.BasicAuth(self._username, self._password)
            self._session = aiohttp.ClientSession(auth=auth)
        return self._session

    async def close(self) -> None:
        if self._own_session and self._session:
            await self._session.close()
            self._session = None

    async def _fetch(self, path: str) -> str:
        session = await self._get_session()
        url = f"{self._base}{path}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                r.raise_for_status()
                return await r.text()
        except aiohttp.ClientResponseError as err:
            raise ConnectionError(f"HTTP {err.status} from {url}") from err
        except aiohttp.ClientError as err:
            raise ConnectionError(f"Cannot reach router at {url}: {err}") from err

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def test_connection(self) -> bool:
        """Return True if the router is reachable and credentials work."""
        try:
            await self._fetch("/Status_Router.live.asp")
            return True
        except ConnectionError:
            return False

    async def async_get_data(self) -> DDWRTData:
        """Fetch and return all router data."""
        router_raw, wireless_raw = await self._fetch(
            "/Status_Router.live.asp"
        ), await self._fetch("/Status_Wireless.live.asp")

        r = _parse_live(router_raw)
        w = _parse_live(wireless_raw)

        # Memory
        mem_used = _safe_int(r.get("mem_used", "0"))
        mem_free = _safe_int(r.get("mem_free", "0"))
        mem_total = mem_used + mem_free

        data = DDWRTData(
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
        return data


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
