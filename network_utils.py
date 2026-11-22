"""Network-related validation shared by the HTTP servers."""
from __future__ import annotations

import ipaddress


_PRIVATE_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


def validate_bind_host(host: str) -> str:
    """Validate a server bind host and return it unchanged.

    Only localhost, loopback IP literals, and explicit private LAN IP literals
    are accepted. Hostnames other than localhost are intentionally rejected so
    an arbitrary DNS name cannot silently select a public interface.
    """
    if not isinstance(host, str) or not host or host != host.strip():
        raise ValueError(
            "bind host must be localhost, a loopback IP, or an explicit private "
            "LAN IP; wildcard, public, and unknown hosts are not allowed"
        )

    if host.lower() == "localhost":
        return host

    # Scoped IPv6 literals are not needed for an explicit private LAN bind and
    # can make interface selection ambiguous across platforms.
    if "%" in host:
        raise ValueError(
            "scoped IP addresses are not allowed; use an unscoped private LAN IP"
        )

    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            "unknown hostnames are not allowed; use localhost, a loopback IP, "
            "or an explicit private LAN IP"
        ) from exc

    if address.is_loopback:
        return host

    if any(address.version == network.version and address in network
           for network in _PRIVATE_LAN_NETWORKS):
        return host

    raise ValueError(
        "bind host must be localhost, a loopback IP, or an explicit private "
        "LAN IP (RFC1918/IPv6 ULA); wildcard and public IP addresses are not allowed"
    )
