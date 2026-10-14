"""HTTPS-only staging probes using the Python standard library."""

from __future__ import annotations

import ipaddress
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .engine import safe_value


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ok: bool
    status: str
    error_type: str
    latency_ms: int


def validate_staging_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip().rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("staging URL must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("staging URL contains forbidden components")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local):
        raise ValueError("staging URL cannot use a private address")
    if "staging" not in parsed.hostname.lower():
        raise ValueError("safety stop: URL is not explicitly staging")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def probe(name: str, url: str, *, timeout_seconds: float = 75.0) -> ProbeResult:
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": "finmentor-staging-monitor/1"})
    status = "unavailable"
    error_type = "none"
    ok = False
    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=context) as response:
            code = int(response.status)
        status = f"http_{code}"
        ok = code == 200
        if not ok:
            error_type = "http_status"
    except urllib.error.HTTPError as exc:
        status = f"http_{int(exc.code)}"
        error_type = "http_status"
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            error_type = "tls_error"
        elif isinstance(reason, (TimeoutError, socket.timeout)):
            error_type = "timeout"
        else:
            error_type = "network_error"
    except (TimeoutError, socket.timeout):
        error_type = "timeout"
    except ssl.SSLError:
        error_type = "tls_error"
    latency = int((time.perf_counter() - started) * 1000)
    return ProbeResult(name, ok, safe_value(status), safe_value(error_type), latency)


def run_staging_probes(frontend_url: str, backend_url: str) -> list[ProbeResult]:
    frontend = validate_staging_base_url(frontend_url)
    backend = validate_staging_base_url(backend_url)
    return [
        probe("frontend", frontend),
        probe("health", f"{backend}/health"),
        probe("readiness", f"{backend}/readiness"),
    ]
