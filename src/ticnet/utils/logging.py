"""Structured, privacy-aware logging framework for TicNet.

Provides idempotent logger configuration, JSON or text formatting, and redaction of sensitive
hardware identifiers (MAC addresses), public WAN IP addresses, and authentication credentials.

Data Minimization & Redaction Policy:
-------------------------------------
1. Redacted Fields:
   - Hardware MAC Addresses: Masked to 'AA:BB:CC:**:**:**'
   - Public WAN IP Addresses: Global routable IPv4 addresses masked to 'X.Y.Z.***'
   - Authentication Secrets: Passwords, tokens, API keys, and bearer tokens masked to '[REDACTED]'
   - Exception tracebacks: Scrubbed through the redaction filter before output

2. Retained Fields:
   - Private RFC 1918 IPv4 addresses (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
   - Loopback (127.0.0.1, ::1) and link-local (169.254.0.0/16) addresses for local diagnostic context
   - Timestamps (UTC ISO 8601), log level, logger name, event messages, diagnostic metrics

3. Omitted Data:
   - Packet payloads, HTTP headers, cookies, environment variables, full configuration dumps

4. Known Limitations:
   - Regex-based masking alone does not provide complete privacy protection if sensitive data
     is encoded, obfuscated, or presented in non-standard representations.
"""

from __future__ import annotations

import datetime
from ticnet.core.timezone import IST
import ipaddress
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union

# Regex Patterns for Sensitive Data Redaction
MAC_PATTERN = re.compile(r"\b([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})\b")

IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

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
    """Mask public WAN IPv4 addresses to X.Y.Z.*** while preserving private/local addresses."""
    def _repl(match: re.Match) -> str:
        ip_str = match.group(0)
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            # Mask any non-local/WAN IP address (including global public and test WAN addresses)
            if not is_local_address(ip_obj):
                parts = ip_str.split(".")
                if len(parts) == 4:
                    return f"{parts[0]}.{parts[1]}.{parts[2]}.***"
                return "[REDACTED_IP]"
            # Private RFC 1918, loopback, and link-local preserved for LAN diagnostic context
            return ip_str
        except ValueError:
            return ip_str

    return IPV4_PATTERN.sub(_repl, text)


def mask_credentials(text: str) -> str:
    """Mask credentials, tokens, and authorization values."""
    def _repl(match: re.Match) -> str:
        key = match.group(1)
        sep = match.group(2)
        quote = match.group(4) or ""
        return f"{key}{sep}[REDACTED]{quote}"

    return SECRET_PATTERN.sub(_repl, text)


def redact_sensitive_text(text: str) -> str:
    """Apply sequential redaction of MACs, credentials, and public IPs to input text."""
    if not text:
        return ""
    text = mask_credentials(text)
    text = mask_mac_address(text)
    text = mask_public_ip_address(text)
    return text


class RedactingFormatter(logging.Formatter):
    """Custom logging formatter that redacts sensitive identifiers and supports JSON."""

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        json_output: bool = False,
        redact: bool = True,
    ) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.json_output = json_output
        self.redact = redact

    def formatException(self, ei) -> str:
        """Format an exception traceback and scrub sensitive identifiers."""
        raw_exc = super().formatException(ei)
        return redact_sensitive_text(raw_exc) if self.redact else raw_exc

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        """Format log record timestamp strictly in Indian Standard Time (IST — Asia/Kolkata)."""
        dt = datetime.datetime.fromtimestamp(record.created, tz=IST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S IST")

    def format(self, record: logging.LogRecord) -> str:
        # Format and redact standard message
        raw_msg = record.getMessage()
        msg = redact_sensitive_text(raw_msg) if self.redact else raw_msg

        # Redact exception information if present
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)

        if self.json_output:
            log_entry: Dict[str, Any] = {
                "timestamp": datetime.datetime.fromtimestamp(
                    record.created, tz=IST
                ).isoformat(),
                "timezone": "Asia/Kolkata",
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
            }
            if record.exc_text:
                log_entry["exception"] = record.exc_text
            # Include custom extra context if provided
            for key, val in record.__dict__.items():
                if key not in {
                    "args", "asctime", "created", "exc_info", "exc_text", "filename",
                    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
                    "msg", "name", "pathname", "process", "processName", "relativeCreated",
                    "stack_info", "thread", "threadName", "taskName",
                }:
                    val_str = str(val)
                    log_entry[key] = redact_sensitive_text(val_str) if self.redact else val_str
            return json.dumps(log_entry)

        # Standard formatted text
        record.msg = msg
        record.args = ()  # Message already substituted and redacted
        return super().format(record)


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[Union[str, Path]] = None,
    json_output: bool = False,
    force: bool = False,
    redact: bool = True,
) -> logging.Logger:
    """Configure TicNet root logger idempotently with privacy-preserving redacting formatters.
    
    Args:
        level: Logging level (e.g., logging.INFO, logging.DEBUG).
        log_file: Optional path to write log output.
        json_output: If True, emits JSON-formatted structured lines.
        force: If True, clears existing handlers and reconfigures logger.
        redact: If True, scrubs MAC addresses, public IPs, and credentials.
        
    Returns:
        The configured 'ticnet' parent logger.
    """
    logger = logging.getLogger("ticnet")
    logger.setLevel(level)

    # Idempotency check: if handlers already configured and not forcing, return logger
    if logger.handlers and not force:
        return logger

    # If forcing reconfiguration, remove existing handlers
    if force:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    # Formatter configuration
    if json_output:
        formatter = RedactingFormatter(json_output=True, redact=redact)
    else:
        text_fmt = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
        date_fmt = "%Y-%m-%d %H:%M:%S IST"
        formatter = RedactingFormatter(fmt=text_fmt, datefmt=date_fmt, json_output=False, redact=redact)

    # Console Handler (only if standard streams exist, e.g. not in windowed GUI mode)
    if sys.stdout is not None:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    elif sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # File Handler (Optional)
    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(file_path), encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Prevent logs from propagating to root logger if root has separate handlers
    logger.propagate = False

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a namespaced TicNet child logger."""
    if name.startswith("ticnet.") or name == "ticnet":
        return logging.getLogger(name)
    return logging.getLogger(f"ticnet.{name}")
