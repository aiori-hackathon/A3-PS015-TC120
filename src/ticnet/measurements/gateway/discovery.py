"""Platform-independent gateway discovery orchestrator and adapter.

Selects the appropriate OS discovery adapter (Windows or Linux), integrates with
Phase 2 configuration (auto_discover and manual_override_ip), and normalizes results.
"""

from __future__ import annotations

import ipaddress
import sys
from typing import Optional, Tuple

from ticnet.configuration.validator import TargetsConfig, validate_ip_address
from ticnet.measurements.base import GatewayDiscoveryResult, MeasurementAdapter
from ticnet.measurements.gateway.linux import LinuxGatewayDiscovery
from ticnet.measurements.gateway.windows import WindowsGatewayDiscovery
from ticnet.models.enums import Status
from ticnet.utils.logging import get_logger, mask_public_ip_address

logger = get_logger("measurements.gateway.discovery")


class GatewayDiscoveryAdapter(MeasurementAdapter):
    """Orchestrates platform default gateway discovery and manual overrides."""

    def __init__(
        self,
        timeout_s: float = 3.0,
        windows_discovery: Optional[WindowsGatewayDiscovery] = None,
        linux_discovery: Optional[LinuxGatewayDiscovery] = None,
    ) -> None:
        super().__init__(timeout_s=timeout_s)
        self._windows_discovery = windows_discovery or WindowsGatewayDiscovery(timeout_s=timeout_s)
        self._linux_discovery = linux_discovery or LinuxGatewayDiscovery(timeout_s=timeout_s)

    @property
    def adapter_name(self) -> str:
        return "GatewayDiscoveryAdapter"

    @property
    def supported_platforms(self) -> Tuple[str, ...]:
        return ("win32", "linux")

    def execute(
        self,
        config: Optional[TargetsConfig] = None,
        manual_override_ip: Optional[str] = None,
        auto_discover: Optional[bool] = None,
    ) -> GatewayDiscoveryResult:
        """Discover the default gateway using configuration and platform discovery.
        
        Resolution Priority:
        1. Manual Override: If manual_override_ip is configured (or passed), validate it
           offline and return it as the selected gateway (discovery_source="MANUAL_OVERRIDE").
        2. Auto-Discovery: If auto_discover is True, invoke the native OS routing table parser.
        3. Incomplete: If auto_discover is False and no override is present, return INCOMPLETE.
        4. Unsupported: If running on an unsupported operating system, return UNSUPPORTED.
        
        Args:
            config: Optional validated TargetsConfig from Phase 2 configuration loader.
            manual_override_ip: Optional explicit override IP string.
            auto_discover: Optional explicit auto-discovery flag.
            
        Returns:
            Normalized GatewayDiscoveryResult instance.
        """
        # Determine configuration settings
        target_override: Optional[str] = manual_override_ip
        should_auto_discover = True

        if config is not None:
            if target_override is None and config.gateway.manual_override_ip:
                target_override = config.gateway.manual_override_ip
            if auto_discover is None:
                should_auto_discover = config.gateway.auto_discover
        elif auto_discover is not None:
            should_auto_discover = auto_discover

        # 1. Handle Manual Override
        if target_override and target_override.strip():
            cleaned_ip = target_override.strip()
            try:
                ip_obj = ipaddress.ip_address(cleaned_ip)
                family = "IPv6" if ip_obj.version == 6 else "IPv4"
                logger.info(
                    "Using configured manual gateway override: %s (%s)",
                    mask_public_ip_address(str(ip_obj)),
                    family,
                )
                return GatewayDiscoveryResult(
                    status=Status.SUCCESS,
                    gateway_ip=str(ip_obj),
                    ip_family=family,
                    discovery_source="MANUAL_OVERRIDE",
                    interface_name=None,
                    local_ip=None,
                    metric=None,
                    error_message=None,
                    raw_route_info={"source": "manual_override", "ip": str(ip_obj)},
                )
            except ValueError:
                logger.error("Invalid manual override IP specified: %s", cleaned_ip)
                return GatewayDiscoveryResult(
                    status=Status.FAILED,
                    error_message=f"Manual gateway override is an invalid IP address: '{cleaned_ip}'",
                    discovery_source="MANUAL_OVERRIDE",
                )

        # 2. Check if auto-discovery is disabled
        if not should_auto_discover:
            logger.warning("Gateway auto-discovery is disabled and no manual override is configured")
            return GatewayDiscoveryResult(
                status=Status.INCOMPLETE,
                error_message="Gateway auto-discovery is disabled in configuration and no manual override IP provided",
                discovery_source="DISABLED",
            )

        # 3. Check platform support
        if sys.platform.startswith("win32"):
            return self._windows_discovery.discover()
        elif sys.platform.startswith("linux"):
            return self._linux_discovery.discover()
        else:
            logger.warning("Gateway discovery requested on unsupported platform: %s", sys.platform)
            return GatewayDiscoveryResult(
                status=Status.UNSUPPORTED,
                error_message=f"Gateway discovery is unsupported on platform '{sys.platform}'",
            )


def discover_gateway(
    config: Optional[TargetsConfig] = None,
    timeout_s: float = 3.0,
) -> GatewayDiscoveryResult:
    """Convenience helper to discover default gateway."""
    adapter = GatewayDiscoveryAdapter(timeout_s=timeout_s)
    return adapter.execute(config=config)
