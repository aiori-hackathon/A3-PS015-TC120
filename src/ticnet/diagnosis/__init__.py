"""Diagnostic rule engine and recommendation generator."""

from ticnet.diagnosis.context import DiagnosisContext
from ticnet.diagnosis.engine import DiagnosisEngine
from ticnet.diagnosis.recommender import ActionItem, FindingRecommendation, RecommendationEngine
from ticnet.diagnosis.rules.base import BaseDiagnosticRule, RuleEvaluationResult
from ticnet.diagnosis.rules.bufferbloat import BufferbloatRule
from ticnet.diagnosis.rules.dns import DNSSubsystemFailureRule
from ticnet.diagnosis.rules.gateway import GatewayUnreachableRule
from ticnet.diagnosis.rules.inconclusive import InconclusiveRule
from ticnet.diagnosis.rules.local import LocalNetworkDegradationRule
from ticnet.diagnosis.rules.loss import ExternalPathDegradationRule

__all__ = [
    "DiagnosisContext",
    "DiagnosisEngine",
    "RecommendationEngine",
    "ActionItem",
    "FindingRecommendation",
    "BaseDiagnosticRule",
    "RuleEvaluationResult",
    "GatewayUnreachableRule",
    "DNSSubsystemFailureRule",
    "ExternalPathDegradationRule",
    "LocalNetworkDegradationRule",
    "BufferbloatRule",
    "InconclusiveRule",
]
