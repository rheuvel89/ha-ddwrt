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

        mem_used = _safe_int(r.get("mem_used", "0"))
        mem_free = _safe_int(r.get("mem_free", "0"))

        return DDWRTData(
            router_name=r.get("router_name", "DD-WRT"),
            wan_ipaddr=r.get("wan_ipaddr", ""),
            wan_status=r.get("wan_status", ""),
            wan_proto=r.get("wan_proto", ""),
            uptime=r.get("uptime", ""),
            load_avg=r.get("load_avg", ""),
            mem_used=mem_used,
            mem_free=mem_free,
            mem_total=mem_used + mem_free,
            lan_ipaddr=r.get("lan_ipaddr", ""),
            wl_ssid=w.get("wl_ssid", ""),
            wl_channel=w.get("wl_channel", ""),
            wl_radio=w.get("wl_radio", ""),
            wl_rate=w.get("wl_rate", ""),
            wl_clients=_parse_clients(w.get("active_wireless", "")),
            dhcp_leases=_parse_dhcp(r.get("dhcp_leases", "")),
        )


class AuthError(ConnectionError):
    """Raised on HTTP 401."""


def _safe_int(value: str) -> int:
    try:
        return int(value.replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0


def _parse_clients(raw: str) -> list[dict[str, str]]:
    if not raw:
        return []
    fields = [f.strip().strip("'") for f in raw.split(",")]
    clients: list[dict[str, str]] = []
    for i in range(0, len(fields) - 8, 9):
        c = fields[i : i + 9]
        clients.append({
            "mac": c[0], "interface": c[1], "uptime": c[2],
            "tx_rate": c[3], "rx_rate": c[4], "signal": c[5],
            "noise": c[6], "snr": c[7], "quality": c[8],
        })
    return clients


def _parse_dhcp(raw: str) -> list[dict[str, str]]:
    if not raw:
        return []
    fields = [f.strip().strip("'") for f in raw.split(",")]
    leases: list[dict[str, str]] = []
    for i in range(0, len(fields) - 3, 4):
        c = fields[i : i + 4]
        leases.append({
            "hostname": c[0], "mac": c[1], "ip": c[2], "expires": c[3],
        })
    return leases
