#!/usr/bin/env python3
"""
DD-WRT diagnostic script — dumps every key/value the router exposes.

Run from the repo root (no HA needed):
    python3 diagnose.py <host> <username> <password> [port] [--ssl]

Example:
    python3 diagnose.py 192.168.1.1 admin secret
    python3 diagnose.py 192.168.1.1 admin secret 443 --ssl

This prints all parsed keys from both live.asp pages and the raw DHCP/WiFi
blobs so we can identify the exact key names your firmware uses.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import re
import ssl as _ssl_module
import sys

try:
    import aiohttp
except ImportError:
    print("aiohttp not installed — run: pip3 install aiohttp")
    sys.exit(1)

_KV_RE = re.compile(r"\{(\w+)::([^}]*)\}")


def _parse_live(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in _KV_RE.finditer(text)}


async def main(host: str, username: str, password: str, port: int, use_ssl: bool) -> None:
    scheme = "https" if use_ssl else "http"
    base = f"{scheme}://{host}:{port}"
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}

    ssl_ctx: bool | _ssl_module.SSLContext = False
    if use_ssl:
        ssl_ctx = _ssl_module.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl_module.CERT_NONE

    async with aiohttp.ClientSession() as session:
        for path in [
            "/Status_Router.live.asp",
            "/Status_Wireless.live.asp",
            "/Status_Lan.live.asp",
            "/Status_Internet.live.asp",
        ]:
            url = base + path
            print(f"\n{'='*70}")
            print(f"  {url}")
            print(f"{'='*70}")
            try:
                async with session.get(
                    url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                    ssl=ssl_ctx,
                ) as resp:
                    print(f"  HTTP {resp.status}")
                    if resp.status == 401:
                        print("  !! Authentication failed — check username/password")
                        continue
                    text = await resp.text()
            except Exception as e:
                print(f"  !! Connection error: {e}")
                continue

            parsed = _parse_live(text)
            if not parsed:
                print("  !! No {key::value} pairs found in response!")
                print(f"  Raw (first 800 chars):\n{text[:800]}")
                continue

            print(f"\n  Parsed {len(parsed)} keys:\n")
            for k, v in sorted(parsed.items()):
                # Truncate long blob values (DHCP/WiFi client lists)
                display = v if len(v) < 200 else v[:200] + "…"
                print(f"    {k:30s} = {display!r}")

            # Special: show DHCP blob field count
            if "dhcp_leases" in parsed:
                blob = parsed["dhcp_leases"]
                fields = [f.strip().strip("'") for f in blob.split(",")]
                print(f"\n  dhcp_leases blob: {len(fields)} comma-separated fields")
                print(f"  → likely stride: {5 if len(fields) % 5 == 0 else 4 if len(fields) % 4 == 0 else '?'}")
                print(f"  First 5 fields: {fields[:5]}")

            # Special: show wireless blob field count
            if "active_wireless" in parsed:
                blob = parsed["active_wireless"]
                fields = [f.strip().strip("'") for f in blob.split(",")]
                print(f"\n  active_wireless blob: {len(fields)} comma-separated fields")
                print(f"  → likely stride: {9 if len(fields) % 9 == 0 else '?'} (expected 9 per client)")
                print(f"  First 9 fields: {fields[:9]}")

            # Special: show active clients / ARP table blob
            _ARP_KEY_NAMES = (
                "active_clients", "arp_table", "lan_arp",
                "activeclients", "arptable", "client_table",
            )
            found_arp = False
            for arp_key in _ARP_KEY_NAMES:
                if arp_key in parsed:
                    blob = parsed[arp_key]
                    fields = [f.strip().strip("'") for f in blob.split(",")]
                    print(f"\n  ✓ ARP/active-client key found: '{arp_key}'")
                    print(f"    blob: {len(fields)} comma-separated fields")
                    for stride in (3, 4, 5):
                        if len(fields) % stride == 0:
                            print(f"    → field count divisible by {stride}")
                    print(f"    First 12 fields: {fields[:12]}")
                    print(f"    Full raw value: {blob!r}")
                    found_arp = True
                    break
            if not found_arp:
                print(f"\n  ✗ No ARP/active-client key found (tried: {_ARP_KEY_NAMES})")
                print(f"    All keys on this page: {sorted(parsed.keys())}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Dump DD-WRT live.asp keys and values")
    p.add_argument("host")
    p.add_argument("username")
    p.add_argument("password")
    p.add_argument("port", nargs="?", type=int, default=80)
    p.add_argument("--ssl", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.host, args.username, args.password, args.port, args.ssl))
