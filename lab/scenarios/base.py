"""Base class and interfaces for controlled laboratory network fault scenarios.

Each scenario implements:
- inject(): introduce the controlled network impairment.
- verify_injection(): independently assert that the impairment is active.
- restore(): restore network to clean baseline state.
- verify_cleanup(): assert 0% residual impairment post-restore.
- ground_truth metadata: label, expected rule, expected category.

Reference: 09_LAB_SAFETY_RUNBOOK.md, SRS-F-015, TC-LAB-01 through TC-LAB-04
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from ticnet.models.enums import FindingCategory, LabScenario


class LabScenarioBase(ABC):
    """Abstract base class for controlled network fault scenarios."""

    scenario_id: LabScenario
    name: str
    description: str

    def __init__(self) -> None:
        self._is_active: bool = False
        self._injection_params: Dict[str, Any] = {}

    @property
    def is_active(self) -> bool:
        """Return True if fault injection is currently active."""
        return self._is_active

    @property
    def injection_parameters(self) -> Dict[str, Any]:
        """Return the parameters applied during fault injection."""
        return dict(self._injection_params)

    @abstractmethod
    def inject(self, **kwargs) -> Dict[str, Any]:
        """Apply the controlled network fault.

        Returns:
            Dictionary of applied fault parameters for ground truth recording.
        """
        pass

    @abstractmethod
    def verify_injection(self) -> bool:
        """Independently verify that the fault condition is genuinely present."""
        pass

    @abstractmethod
    def restore(self) -> bool:
        """Remove the fault and restore clean baseline conditions.

        Returns:
            True if restoration command succeeded.
        """
        pass

    @abstractmethod
    def verify_cleanup(self) -> bool:
        """Independently verify that baseline health is 100% restored with zero residual impairment."""
        pass

    @abstractmethod
    def get_ground_truth_label(self) -> str:
        """Return human-readable ground truth description."""
        pass

    @abstractmethod
    def get_expected_rule_id(self) -> str:
        """Return the expected diagnostic rule ID that should fire (e.g. R-GW-01)."""
        pass

    @abstractmethod
    def get_expected_category(self) -> FindingCategory:
        """Return the expected finding category (e.g. LOCAL_NETWORK)."""
        pass

    def create_adapters(self) -> Dict[str, Any]:
        """Optionally provide customized measurement adapters for lab runner execution."""
        return {}
