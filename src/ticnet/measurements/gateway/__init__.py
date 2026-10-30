"""Gateway and default route discovery package for TicNet."""

from ticnet.measurements.gateway.discovery import (
    GatewayDiscoveryAdapter,
    discover_gateway,
)
from ticnet.measurements.gateway.linux import (
    LinuxGatewayDiscovery,
    LinuxRouteCandidate,
)
from ticnet.measurements.gateway.windows import (
    WindowsGatewayDiscovery,
    WindowsRouteCandidate,
)

__all__ = [
    "GatewayDiscoveryAdapter",
    "LinuxGatewayDiscovery",
    "LinuxRouteCandidate",
    "WindowsGatewayDiscovery",
    "WindowsRouteCandidate",
    "discover_gateway",
]
