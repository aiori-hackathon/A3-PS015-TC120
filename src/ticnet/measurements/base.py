"""Abstract base measurement adapter and normalized result models for TicNet.

Establishes a strongly-typed, cross-platform contract for all network adapters:
- Safe, tokenized subprocess execution (strictly shell=False).
- Finite, bounded execution timeouts.
- Integration with domain Status and TargetType enums.
- Explicit UTC timestamps and non-zero null invariants.
- Normalized discovery result structures.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
import datetime
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from ticnet.models.entities import Target, new_id, now_utc_iso
from ticnet.models.enums import Status, TargetType
from ticnet.utils.logging import get_logger, mask_public_ip_address

logger = get_logger("measurements.base")


@dataclass(frozen=True)
class GatewayDiscoveryResult:
    """Normalized output from platform default gateway discovery.
    
    Invariant: Discovery does NOT imply reachability.
    Reachability must only be confirmed via subsequent ICMP probing in Phase 4.
    """
    status: Status
    gateway_ip: Optional[str] = None
    ip_family: Optional[str] = None  # "IPv4" or "IPv6"
    interface_name: Optional[str] = None
    interface_id: Optional[str] = None
    local_ip: Optional[str] = None
    metric: Optional[int] = None
    discovery_source: str = "OS_ROUTE_TABLE"  # "OS_ROUTE_TABLE" or "MANUAL_OVERRIDE"
    timestamp_utc: str = field(default_factory=now_utc_iso)
    error_message: Optional[str] = None
    raw_route_info: Optional[Dict[str, Any]] = None

    def to_target(self) -> Optional[Target]:
        """Convert a successful gateway discovery into a Target entity."""
        if self.status != Status.SUCCESS or not self.gateway_ip:
            return None
        return Target(
            target_id=new_id(),
            target_type=TargetType.GATEWAY,
            ip_address=self.gateway_ip,
            provider_label=self.interface_name or "Default Gateway",
            is_active=True,
        )


class MeasurementAdapter(abc.ABC):
    """Abstract base class for all TicNet measurement and discovery adapters."""

    def __init__(self, timeout_s: float = 3.0) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be a positive number")
        self.timeout_s = timeout_s

    @property
    @abc.abstractmethod
    def adapter_name(self) -> str:
        """Human-readable identifier of the adapter."""
        pass

    @property
    @abc.abstractmethod
    def supported_platforms(self) -> Tuple[str, ...]:
        """Tuple of sys.platform identifiers supported by this adapter (e.g. ('win32',))."""
        pass

    def is_supported_on_current_platform(self) -> bool:
        """Check whether current operating system matches supported platforms."""
        return any(sys.platform.startswith(p) for p in self.supported_platforms)

    def run_command(
        self,
        cmd: List[str],
        timeout_s: Optional[float] = None,
    ) -> Tuple[int, str, str]:
        """Execute an operating-system utility safely with tokenized arguments.
        
        Security and Safety Guarantees:
        1. strictly shell=False (eliminates shell injection attack surface).
        2. Finite, bounded timeout to prevent indefinite process hangs.
        3. Captures stdout and stderr safely using replace error handling.
        4. Does not modify routing, firewall, or network interfaces.
        
        Args:
            cmd: Tokenized command argument list (e.g. ['route', 'print', '0.0.0.0']).
            timeout_s: Timeout in seconds (defaults to self.timeout_s).
            
        Returns:
            Tuple of (exit_code, stdout_str, stderr_str).
            
        Raises:
            subprocess.TimeoutExpired: When command execution exceeds timeout.
            FileNotFoundError: When target binary is not present in PATH.
            PermissionError: When system denies unprivileged execution.
        """
        if not isinstance(cmd, list) or len(cmd) == 0:
            raise ValueError("Command must be a non-empty list of argument strings")
        for arg in cmd:
            if not isinstance(arg, str):
                raise ValueError(f"All command arguments must be strings, got: {type(arg).__name__}")

        timeout = timeout_s if timeout_s is not None else self.timeout_s

        logger.debug("Executing command: %s (timeout=%ss)", " ".join(cmd), timeout)

        # Enforce shell=False unconditionally and suppress terminal window creation on Windows
        kwargs: Dict[str, Any] = {
            "shell": False,
            "capture_output": True,
            "timeout": timeout,
            "check": False,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = startupinfo

        proc = subprocess.run(cmd, **kwargs)

        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        return proc.returncode, stdout, stderr

    @abc.abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the adapter's primary operation and return a typed result."""
        pass
