"""Privacy safeguards and data minimization for report exports.

Implements SRS-F-018, AC-PERS-05:
- Hardware MAC addresses masked to 'AA:BB:CC:**:**:**'
- Public WAN IP addresses masked to 'X.Y.Z.***'
- Private RFC 1918, loopback, and link-local addresses preserved for LAN diagnostic context
- Secret credentials/tokens redacted to '[REDACTED]'
- Configurable PrivacyFilter class supporting selective redaction
"""

from __future__ import annotations

from dataclasses import dataclass, field, is_dataclass, asdict, replace
import ipaddress
import re
from typing import Any, Dict, List, Optional, Union


MAC_PATTERN = re.compile(r"\b([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})\b")
IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6_PATTERN = re.compile(
    r"(?<![0-9a-fA-F:])"
    r"(?:"
    r"(?:[0-9a-fA-F]{1,4}:){1,7}:(?:[0-9a-fA-F]{1,4}:)*[0-9a-fA-F]{1,4}"
    r"|(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"
    r"|::(?:[0-9a-fA-F]{1,4}:)*[0-9a-fA-F]{1,4}"
    r"|[0-9a-fA-F]{1,4}::"
    r"|::1"
    r")"
    r"(?![0-9a-fA-F:])"
)
SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|bearer|authorization)\b(\s*[:=]\s*['\"]?)([^'\"\s,;]+)(['\"]?)"
)

LOCAL_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),  # RFC 4193 Unique Local IPv6 (Private LAN)
)


def is_local_address(ip_obj: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    """Return True if IP address belongs to RFC 1918, loopback, or link-local networks."""
    return any(ip_obj in net for net in LOCAL_NETWORKS)


def mask_mac_address(text: str) -> str:
    """Mask hardware MAC addresses preserving vendor prefix (OUI): AA:BB:CC:**:**:**."""
    def _repl(match: re.Match) -> str:
        mac = match.group(0)
        sep = ":" if ":" in mac else "-"
        parts = mac.split(sep)
        if len(parts) == 6:
            return f"{parts[0]}{sep}{parts[1]}{sep}{parts[2]}{sep}**{sep}**{sep}**"
        return "[REDACTED_MAC]"

    return MAC_PATTERN.sub(_repl, text)


def mask_public_ip_address(text: str) -> str:
    """Mask public WAN IPv4 and IPv6 addresses while preserving private/local addresses."""
    # 1. Mask IPv4
    def _repl_ipv4(match: re.Match) -> str:
        ip_str = match.group(0)
        try:
            ip_obj = ipaddress.IPv4Address(ip_str)
            # Mask any non-local/WAN IP address
            if not is_local_address(ip_obj):
                parts = ip_str.split(".")
                if len(parts) == 4:
                    return f"{parts[0]}.{parts[1]}.{parts[2]}.***"
                return "[REDACTED_IP]"
            # Private RFC 1918, loopback, and link-local preserved for LAN diagnostic context
            return ip_str
        except ValueError:
            return ip_str

    res = IPV4_PATTERN.sub(_repl_ipv4, text)

    # 2. Mask IPv6
    def _repl_ipv6(match: re.Match) -> str:
        ip_str = match.group(0)
        try:
            ip_obj = ipaddress.IPv6Address(ip_str)
            if not is_local_address(ip_obj):
                exploded = ip_obj.exploded.split(":")
                return f"{exploded[0]}:{exploded[1]}:{exploded[2]}:****:****:****:****:****"
            return ip_str
        except ValueError:
            return ip_str

    return IPV6_PATTERN.sub(_repl_ipv6, res)


def mask_credentials(text: str) -> str:
    """Mask credentials, tokens, and authorization values."""
    def _repl(match: re.Match) -> str:
        key = match.group(1)
        sep = match.group(2)
        quote = match.group(4) or ""
        return f"{key}{sep}[REDACTED]{quote}"

    return SECRET_PATTERN.sub(_repl, text)


@dataclass(frozen=True)
class PrivacyFilter:
    """Configurable privacy filter options for export serialization (SRS-F-018, AC-PERS-05)."""

    mask_mac_addresses: bool = True
    mask_public_ips: bool = True
    mask_credentials: bool = True
    custom_replacements: Optional[Dict[str, str]] = None

    def apply_to_text(self, text: str) -> str:
        """Apply active privacy masking filters to a text string."""
        if not text:
            return text

        result = text
        if self.mask_credentials:
            result = mask_credentials(result)
        if self.mask_mac_addresses:
            result = mask_mac_address(result)
        if self.mask_public_ips:
            result = mask_public_ip_address(result)
        if self.custom_replacements:
            for pattern, replacement in self.custom_replacements.items():
                result = re.sub(pattern, replacement, result)
        return result

    def apply_to_obj(self, obj: Any) -> Any:
        """Recursively apply privacy filtering to strings in dicts, lists, or dataclasses."""
        if obj is None or isinstance(obj, (int, float, bool)):
            return obj
        if isinstance(obj, str):
            return self.apply_to_text(obj)
        if isinstance(obj, list):
            return [self.apply_to_obj(item) for item in obj]
        if isinstance(obj, dict):
            return {k: self.apply_to_obj(v) for k, v in obj.items()}
        if isinstance(obj, tuple):
            return tuple(self.apply_to_obj(item) for item in obj)
        if is_dataclass(obj) and not isinstance(obj, type):
            # Process dataclass instance
            return self.apply_to_obj(asdict(obj))
        return obj


DEFAULT_PRIVACY_FILTER = PrivacyFilter(
    mask_mac_addresses=True,
    mask_public_ips=True,
    mask_credentials=True,
)
