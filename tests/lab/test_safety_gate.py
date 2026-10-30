"""Tests for Automated Laboratory Safety Gate (TC-LAB-05, AC-LAB-01).

Verifies fail-closed behavior, interface quarantine, public IP rejection,
operator affirmation enforcement, and emergency cleanup handler registration.
"""

from __future__ import annotations

import pytest

from lab.safety_gate import LabSafetyGate, SafetyGateError


class TestLabSafetyGate:
    """Verifies TC-LAB-05 and AC-LAB-01."""

    def test_safety_gate_passes_in_quarantined_environment(self) -> None:
        """Verify safety gate passes when all 5 checks are satisfied."""
        gate = LabSafetyGate(
            target_interface="veth-client",
            lab_gateway_ip="127.0.0.1",
            operator_confirmed=True,
        )
        passed, log = gate.verify_all()
        assert passed is True
        assert len(log) == 5
        assert any("Check 4 [PASS]" in entry for entry in log)
        assert any("Check 1 [PASS]" in entry for entry in log)
        assert any("Check 2 [PASS]" in entry for entry in log)
        assert any("Check 3 [PASS]" in entry for entry in log)
        assert any("Check 5 [PASS]" in entry for entry in log)

    def test_safety_gate_aborts_without_operator_affirmation(self) -> None:
        """Verify safety gate aborts immediately if --i-confirm-isolated-lab is omitted."""
        gate = LabSafetyGate(
            target_interface="veth-client",
            lab_gateway_ip="127.0.0.1",
            operator_confirmed=False,
        )
        with pytest.raises(SafetyGateError, match="Operator affirmation flag.*is missing"):
            gate.verify_all()

    def test_safety_gate_aborts_on_production_physical_interface(self) -> None:
        """Verify safety gate aborts if physical NICs like eth0 or Wi-Fi are targeted."""
        for disallowed in ["eth0", "Wi-Fi", "Ethernet", "en0", "wlan0"]:
            gate = LabSafetyGate(
                target_interface=disallowed,
                lab_gateway_ip="127.0.0.1",
                operator_confirmed=True,
            )
            with pytest.raises(SafetyGateError, match="primary physical NIC"):
                gate.verify_all()

    def test_safety_gate_aborts_on_unapproved_interface_prefix(self) -> None:
        """Verify safety gate aborts if interface name lacks allowed virtual prefix."""
        gate = LabSafetyGate(
            target_interface="corporate_lan",
            lab_gateway_ip="127.0.0.1",
            operator_confirmed=True,
        )
        with pytest.raises(SafetyGateError, match="does not match allowed lab interface prefixes"):
            gate.verify_all()

    def test_safety_gate_aborts_on_public_internet_destination(self) -> None:
        """Verify safety gate aborts if destination routes to public internet."""
        for public_ip in ["8.8.8.8", "1.1.1.1", "9.9.9.9", "208.67.222.222"]:
            gate = LabSafetyGate(
                target_interface="veth-client",
                lab_gateway_ip=public_ip,
                operator_confirmed=True,
            )
            with pytest.raises(SafetyGateError, match="falls inside public internet subnet"):
                gate.verify_all()

    def test_safety_gate_cleanup_handler_registration_and_execution(self) -> None:
        """Verify cleanup callbacks are registered and executed upon cleanup trigger."""
        gate = LabSafetyGate(
            target_interface="veth-client",
            lab_gateway_ip="127.0.0.1",
            operator_confirmed=True,
        )
        cleaned = []
        gate.register_cleanup_callback(lambda: cleaned.append("restored"))
        gate.execute_cleanup()
        assert cleaned == ["restored"]
