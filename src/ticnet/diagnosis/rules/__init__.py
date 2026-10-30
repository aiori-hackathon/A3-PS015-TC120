"""Versioned diagnostic rules package."""

from ticnet.diagnosis.rules.base import BaseDiagnosticRule, RuleEvaluationResult
from ticnet.diagnosis.rules.bufferbloat import BufferbloatRule
from ticnet.diagnosis.rules.dns import DNSSubsystemFailureRule
from ticnet.diagnosis.rules.gateway import GatewayUnreachableRule
from ticnet.diagnosis.rules.inconclusive import InconclusiveRule
from ticnet.diagnosis.rules.local import LocalNetworkDegradationRule
from ticnet.diagnosis.rules.loss import ExternalPathDegradationRule

__all__ = [
    "BaseDiagnosticRule",
    "RuleEvaluationResult",
    "GatewayUnreachableRule",
    "DNSSubsystemFailureRule",
    "ExternalPathDegradationRule",
    "LocalNetworkDegradationRule",
    "BufferbloatRule",
    "InconclusiveRule",
]
