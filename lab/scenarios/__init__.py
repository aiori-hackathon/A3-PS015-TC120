"""Laboratory fault scenarios module."""

from lab.scenarios.base import LabScenarioBase
from lab.scenarios.scenario_a_gateway import ScenarioAGateway
from lab.scenarios.scenario_b_dns import ScenarioBDNS
from lab.scenarios.scenario_c_loss import ScenarioCLoss
from lab.scenarios.scenario_d_congestion import ScenarioDCongestion

__all__ = [
    "LabScenarioBase",
    "ScenarioAGateway",
    "ScenarioBDNS",
    "ScenarioCLoss",
    "ScenarioDCongestion",
]
