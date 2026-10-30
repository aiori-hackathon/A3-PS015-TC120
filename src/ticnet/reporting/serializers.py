"""Report serializers for structured JSON, tabular CSV, and human-readable Markdown.

Implements:
- SRS-F-012: Serialized Diagnostic Export (JSON & CSV)
- SRS-F-018: Data Minimization & Redaction
- AC-PERS-03: Deterministic JSON conforming to data model schema
- AC-PERS-04: Tabular CSV preserving sample indices, metric units, null representations
- AC-PERS-05: Configurable privacy filtering
- Mitigation for CSV formula injection without corrupting legitimate negative numeric measurements
- Professional 10-section diagnostic reporting for both CSV and JSON exports
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import io
import json
import math
from typing import Any, Dict, List, Optional, Union

from ticnet.core.timezone import format_ist, now_ist_iso, to_ist
from ticnet.models.entities import (
    TestRun,
    TestStage,
    Target,
    Observation,
    Summary,
    Finding,
    FindingEvidence,
    now_utc_iso,
)
from ticnet.models.enums import (
    RunStatus,
    StageStatus,
    TargetType,
    MeasurementStatus,
    FindingCategory,
    FindingPriority,
    FindingStatus,
    EvidenceStrength,
    EvidenceRelationship,
    ExportFormat,
)
from ticnet.reporting.privacy import PrivacyFilter, DEFAULT_PRIVACY_FILTER


# Trigger characters that spreadsheet applications treat as formulas:
# '=', '+', '-', '@', '\t', '\r'
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_cell(value: Any) -> Any:
    """Sanitize cell value to prevent CSV formula injection while preserving numbers.
    
    Mitigates spreadsheet formula injection (CWE-1236 / OWASP).
    
    Policy:
    1. None -> ""
    2. Finite numeric int/float -> preserved as numeric (e.g. -12.5, 42.0, 0, 0.0)
    3. Non-finite float (nan, inf, -inf) or non-finite string ("nan", "Infinity", etc.) -> ""
    4. Text beginning with tabs, carriage returns, or formula prefixes (=, +, -, @)
       after stripping leading whitespace:
       - If it parses as a finite float (e.g. "-12.5" or "+1.0"), preserved as numeric text.
       - If it is formula payload (e.g. "=1+1", "-2+3*cmd", "@SUM(A1:A2)") or unit-bearing
         text starting with minus (e.g. "-12.5 dBm", "-85 dBm"), prepends single quote (')
         so spreadsheet viewers treat it strictly as literal text without formula evaluation.
    """
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return ""
        return value
    str_val = str(value)
    if not str_val:
        return ""
    stripped = str_val.lstrip(" \t\r\n\v\f")
    if not stripped:
        if any(c in str_val for c in ("\t", "\r")):
            return "'" + str_val
        return str_val
    leading = str_val[: len(str_val) - len(stripped)]
    if any(c in leading for c in ("\t", "\r", "\n", "\v", "\f")):
        return "'" + str_val
    if stripped.lower() in ("nan", "inf", "-inf", "+inf", "infinity", "-infinity", "+infinity"):
        return ""
    if stripped.startswith(FORMULA_PREFIXES):
        try:
            val = float(stripped)
            if math.isfinite(val):
                return str_val
        except ValueError:
            pass
        return "'" + str_val
    return str_val


METRIC_LABELS: Dict[str, str] = {
    "round_trip_time": "Round-Trip Time (ms)",
    "rtt": "Round-Trip Time (ms)",
    "latency_ms": "Round-Trip Latency (ms)",
    "packet_loss": "Packet Loss (%)",
    "packet_loss_pct": "Packet Loss (%)",
    "delay_variation": "Delay Variation / MACD Jitter (ms)",
    "jitter": "Delay Variation / MACD Jitter (ms)",
    "gateway_discovery": "Gateway Route Inspection",
    "dns_query_time": "DNS Resolution Response Time (ms)",
    "dns_resolution_time": "DNS Resolution Response Time (ms)",
    "dns_resolution": "DNS Resolution Status",
    "download_throughput": "Download Throughput (Mbps)",
    "loaded_rtt": "Loaded Round-Trip Time (ms)",
    "loaded_latency": "Loaded Round-Trip Time (ms)",
    "loaded_latency_delta": "Bufferbloat Latency Increase (ms)",
    "bufferbloat_delta": "Bufferbloat Latency Increase (ms)",
    "gateway_ping": "Gateway Ping RTT (ms)",
}


def get_metric_human_label(metric_name: str) -> str:
    """Return a professional, human-readable label for a raw telemetry metric name."""
    if metric_name in METRIC_LABELS:
        return METRIC_LABELS[metric_name]
    return metric_name.replace("_", " ").title()


def format_report_filename(
    run_id: str,
    export_format: Union[ExportFormat, str],
    timestamp_utc: Optional[str] = None,
) -> str:
    """Generate standardized filename conforming to TicNet_Diagnostic_Report_RUNID_YYYY-MM-DD_HH-MM-SS.ext."""
    ext_map = {
        ExportFormat.JSON: ".json",
        ExportFormat.CSV: ".csv",
        ExportFormat.MARKDOWN: ".md",
    }
    fmt: Optional[ExportFormat] = None
    if isinstance(export_format, ExportFormat):
        fmt = export_format
    elif isinstance(export_format, str):
        try:
            fmt = ExportFormat(export_format.upper())
        except ValueError:
            fmt = None

    if fmt is not None and fmt in ext_map:
        ext = ext_map[fmt]
    elif isinstance(export_format, str):
        ext = f".{export_format.lower()}"
    else:
        ext = ".txt"

    ts = timestamp_utc or now_utc_iso()
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        date_str = dt.strftime("%Y-%m-%d_%H-%M-%S")
    except Exception:
        date_str = ts.replace(":", "-").replace("T", "_").split(".")[0].split("+")[0].split("Z")[0]
    return f"TicNet_Diagnostic_Report_{run_id}_{date_str}{ext}"


def _calculate_duration_seconds(start_iso: Optional[str], end_iso: Optional[str]) -> Optional[float]:
    if not start_iso or not end_iso:
        return None
    try:
        s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        diff = (e - s).total_seconds()
        return round(diff, 2) if diff >= 0 else None
    except Exception:
        return None


def _safe_float(val: Any) -> Optional[float]:
    """Return finite float or None for NaN/Inf/None/invalid."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isfinite(f):
            return f
        return None
    except (TypeError, ValueError, OverflowError):
        return None



def _find_stage(stages: List[TestStage], order: int, *keywords: str) -> Optional[TestStage]:
    for s in stages:
        if s.stage_order == order:
            return s
    for s in stages:
        name_lower = (s.stage_name or "").lower()
        if any(kw.lower() in name_lower for kw in keywords):
            return s
    return None


def _find_summary(
    summaries: List[Summary],
    target_id: Optional[str],
    metric_names: List[str],
    stat_names: Optional[List[str]] = None,
) -> Optional[Summary]:
    for s in summaries:
        if target_id is not None and s.target_id != target_id:
            continue
        if s.metric_name in metric_names:
            if stat_names is None or s.statistic_name in stat_names:
                return s
    return None


@dataclass
class DiagnosticReportData:
    """Comprehensive composite report model containing all entities of a diagnostic run."""

    run: TestRun
    stages: List[TestStage] = field(default_factory=list)
    observations: List[Observation] = field(default_factory=list)
    summaries: List[Summary] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    evidence: List[FindingEvidence] = field(default_factory=list)
    targets: Dict[str, Target] = field(default_factory=dict)
    schema_version: str = "1.0.0"
    generated_at_utc: str = field(default_factory=now_utc_iso)


def _extract_report_overview(data: DiagnosticReportData) -> Dict[str, Any]:
    run = data.run
    status_str = run.status.value if hasattr(run.status, "value") else str(run.status)
    return {
        "product_name": "TicNet",
        "product_version": run.app_version or "1.0.0",
        "report_title": "TicNet Comprehensive Diagnostic Report",
        "run_id": run.run_id,
        "report_generated_utc": data.generated_at_utc,
        "execution_start_utc": run.start_time_utc,
        "execution_end_utc": run.end_time_utc,
        "overall_diagnostic_status": status_str,
    }


def _extract_run_summary(data: DiagnosticReportData) -> Dict[str, Any]:
    run = data.run
    duration = _calculate_duration_seconds(run.start_time_utc, run.end_time_utc)
    total_stages = len(data.stages)
    success_count = sum(1 for s in data.stages if s.status == StageStatus.SUCCESS)
    failed_count = sum(1 for s in data.stages if s.status == StageStatus.FAILED)
    skipped_count = sum(1 for s in data.stages if s.status == StageStatus.SKIPPED)
    inconclusive_count = sum(
        1 for s in data.stages if s.status in (StageStatus.PENDING, StageStatus.RUNNING, StageStatus.CANCELLED)
    )

    return {
        "run_id": run.run_id,
        "status": run.status.value if hasattr(run.status, "value") else str(run.status),
        "run_mode": run.run_mode.value if hasattr(run.run_mode, "value") else str(run.run_mode),
        "start_time_utc": run.start_time_utc,
        "end_time_utc": run.end_time_utc,
        "duration_seconds": duration,
        "interface_name": run.interface_name or "Unknown / Unspecified",
        "interface_type": run.interface_type or "Unknown / Unspecified",
        "gateway_ip": run.gateway_ip or "Unavailable",
        "stages_total": total_stages,
        "stages_successful": success_count,
        "stages_failed": failed_count,
        "stages_skipped": skipped_count,
        "stages_inconclusive": inconclusive_count,
        "overall_error": run.overall_error,
    }


def _extract_network_environment(data: DiagnosticReportData) -> Dict[str, Any]:
    run = data.run
    return {
        "active_interface_name": run.interface_name or "Not Available",
        "active_interface_type": run.interface_type or "Not Available",
        "default_gateway_ip": run.gateway_ip or "Not Available",
        "ip_family": "IPv4" if (run.gateway_ip and "." in run.gateway_ip) else ("IPv6" if run.gateway_ip else "Unknown"),
    }


def _extract_gateway_discovery(data: DiagnosticReportData) -> Dict[str, Any]:
    st1 = _find_stage(data.stages, 1, "gateway discovery", "gateway")
    st_status = st1.status.value if st1 and hasattr(st1.status, "value") else (str(st1.status) if st1 else ("SUCCESS" if data.run.gateway_ip else "NOT_RUN"))
    gw_obs = [o for o in data.observations if o.metric_name == "gateway_discovery" or (st1 and o.stage_id == st1.stage_id)]
    
    discovery_method = "Windows Route Table Inspection (Unprivileged)"
    evidence_desc = "Local default network route"
    if gw_obs and gw_obs[0].method_metadata:
        try:
            meta = json.loads(gw_obs[0].method_metadata)
            src = meta.get("source")
            if src:
                discovery_method = str(src)
            iface = meta.get("interface")
            if iface:
                evidence_desc = f"Route table on interface {iface}"
        except Exception:
            pass

    return {
        "stage_name": st1.stage_name if st1 else "Gateway Discovery",
        "status": st_status,
        "gateway_ip": data.run.gateway_ip,
        "discovery_method": discovery_method,
        "timestamp_utc": st1.start_time_utc if st1 else data.run.start_time_utc,
        "evidence_summary": evidence_desc,
        "error_message": st1.error_message if st1 else None,
    }


def _extract_gateway_reachability(data: DiagnosticReportData) -> Dict[str, Any]:
    st2 = _find_stage(data.stages, 2, "gateway reachability", "gateway connectivity")
    st_status = st2.status.value if st2 and hasattr(st2.status, "value") else (str(st2.status) if st2 else "NOT_RUN")
    
    gw_target = next(
        (t for t in data.targets.values() if t.target_type == TargetType.GATEWAY or (data.run.gateway_ip and t.ip_address == data.run.gateway_ip)),
        None,
    )
    
    gw_obs = [
        o for o in data.observations
        if (gw_target and o.target_id == gw_target.target_id)
        or (st2 and o.stage_id == st2.stage_id)
        or ("gw" in (o.target_id or "").lower())
        or (o.metric_name in ("round_trip_time", "rtt", "latency_ms", "gateway_ping") and not any(t.target_id == o.target_id and t.target_type != TargetType.GATEWAY for t in data.targets.values()))
    ]
    
    gw_summaries = [s for s in data.summaries if (gw_target and s.target_id == gw_target.target_id) or "gw" in (s.target_id or "").lower()]
    
    loss_sum = _find_summary(gw_summaries, None, ["packet_loss", "packet_loss_pct"])
    rtt_sum_mean = _find_summary(gw_summaries, None, ["round_trip_time", "rtt", "latency_ms"], ["mean", "avg"])
    rtt_sum_median = _find_summary(gw_summaries, None, ["round_trip_time", "rtt", "latency_ms"], ["median"])
    rtt_sum_min = _find_summary(gw_summaries, None, ["round_trip_time", "rtt", "latency_ms"], ["min"])
    rtt_sum_max = _find_summary(gw_summaries, None, ["round_trip_time", "rtt", "latency_ms"], ["max"])
    rtt_sum_p95 = _find_summary(gw_summaries, None, ["round_trip_time", "rtt", "latency_ms"], ["p95"])
    jitter_sum = _find_summary(gw_summaries, None, ["delay_variation", "jitter", "macd_jitter_ms"])
    
    tx_count = loss_sum.sample_count_total if loss_sum else len(gw_obs)
    rx_count = loss_sum.sample_count_valid if loss_sum else sum(1 for o in gw_obs if o.status == MeasurementStatus.SUCCESS and o.value_numeric is not None)
    
    loss_pct: Optional[float] = None
    if loss_sum and loss_sum.value_numeric is not None:
        loss_pct = _safe_float(loss_sum.value_numeric)
    elif tx_count > 0:
        loss_pct = round(((tx_count - rx_count) / tx_count) * 100.0, 1)

    valid_rtts = [o.value_numeric for o in gw_obs if o.value_numeric is not None and math.isfinite(o.value_numeric)]
    
    min_rtt = _safe_float(rtt_sum_min.value_numeric) if rtt_sum_min else (min(valid_rtts) if valid_rtts else None)
    avg_rtt = _safe_float(rtt_sum_mean.value_numeric) if rtt_sum_mean else (round(sum(valid_rtts) / len(valid_rtts), 2) if valid_rtts else None)
    max_rtt = _safe_float(rtt_sum_max.value_numeric) if rtt_sum_max else (max(valid_rtts) if valid_rtts else None)
    median_rtt = _safe_float(rtt_sum_median.value_numeric) if rtt_sum_median else None
    p95_rtt = _safe_float(rtt_sum_p95.value_numeric) if rtt_sum_p95 else None
    jitter = _safe_float(jitter_sum.value_numeric) if jitter_sum else None
    duration = _calculate_duration_seconds(st2.start_time_utc, st2.end_time_utc) if st2 else None

    return {
        "stage_name": st2.stage_name if st2 else "Gateway Reachability",
        "status": st_status,
        "target_gateway_ip": data.run.gateway_ip or (gw_target.ip_address if gw_target else "Not Available"),
        "packets_transmitted": tx_count,
        "packets_received": rx_count,
        "packet_loss_pct": loss_pct,
        "min_rtt_ms": min_rtt,
        "avg_rtt_ms": avg_rtt,
        "max_rtt_ms": max_rtt,
        "median_rtt_ms": median_rtt,
        "p95_rtt_ms": p95_rtt,
        "delay_variation_ms": jitter,
        "test_duration_seconds": duration,
        "error_message": st2.error_message if st2 else None,
    }


def _extract_multi_target_ping(data: DiagnosticReportData) -> Dict[str, Any]:
    st3 = _find_stage(data.stages, 3, "multi-target", "external", "ping")
    st_status = st3.status.value if st3 and hasattr(st3.status, "value") else (str(st3.status) if st3 else "NOT_RUN")
    
    external_targets = [
        t for t in sorted(data.targets.values(), key=lambda t: t.target_id)
        if t.target_type != TargetType.GATEWAY and not ("gw" in t.target_id.lower())
    ]
    
    target_results: List[Dict[str, Any]] = []
    for tgt in external_targets:
        tgt_obs = [o for o in data.observations if o.target_id == tgt.target_id]
        tgt_sums = [s for s in data.summaries if s.target_id == tgt.target_id]
        
        loss_sum = _find_summary(tgt_sums, tgt.target_id, ["packet_loss", "packet_loss_pct"])
        rtt_sum_mean = _find_summary(tgt_sums, tgt.target_id, ["round_trip_time", "rtt", "latency_ms"], ["mean", "avg"])
        rtt_sum_median = _find_summary(tgt_sums, tgt.target_id, ["round_trip_time", "rtt", "latency_ms"], ["median"])
        rtt_sum_min = _find_summary(tgt_sums, tgt.target_id, ["round_trip_time", "rtt", "latency_ms"], ["min"])
        rtt_sum_max = _find_summary(tgt_sums, tgt.target_id, ["round_trip_time", "rtt", "latency_ms"], ["max"])
        rtt_sum_p95 = _find_summary(tgt_sums, tgt.target_id, ["round_trip_time", "rtt", "latency_ms"], ["p95"])
        jitter_sum = _find_summary(tgt_sums, tgt.target_id, ["delay_variation", "jitter", "macd_jitter_ms"])
        
        tx = loss_sum.sample_count_total if loss_sum else len(tgt_obs)
        rx = loss_sum.sample_count_valid if loss_sum else sum(1 for o in tgt_obs if o.status == MeasurementStatus.SUCCESS and o.value_numeric is not None)
        
        loss_pct: Optional[float] = None
        if loss_sum and loss_sum.value_numeric is not None:
            loss_pct = _safe_float(loss_sum.value_numeric)
        elif tx > 0:
            loss_pct = round(((tx - rx) / tx) * 100.0, 1)

        valid_rtts = [o.value_numeric for o in tgt_obs if o.value_numeric is not None and math.isfinite(o.value_numeric)]
        min_lat = _safe_float(rtt_sum_min.value_numeric) if rtt_sum_min else (min(valid_rtts) if valid_rtts else None)
        avg_lat = _safe_float(rtt_sum_mean.value_numeric) if rtt_sum_mean else (round(sum(valid_rtts) / len(valid_rtts), 2) if valid_rtts else None)
        max_lat = _safe_float(rtt_sum_max.value_numeric) if rtt_sum_max else (max(valid_rtts) if valid_rtts else None)
        med_lat = _safe_float(rtt_sum_median.value_numeric) if rtt_sum_median else None
        p95_lat = _safe_float(rtt_sum_p95.value_numeric) if rtt_sum_p95 else None
        jitter = _safe_float(jitter_sum.value_numeric) if jitter_sum else None
        
        if loss_pct == 0.0:
            outcome = "SUCCESS"
        elif loss_pct is not None and loss_pct < 100.0:
            outcome = "DEGRADED (Packet Loss)"
        elif loss_pct == 100.0:
            outcome = "FAILED (100% Loss)"
        else:
            outcome = "NOT_EVALUATED"
            
        target_results.append({
            "target_id": tgt.target_id,
            "hostname": tgt.hostname,
            "ip_address": tgt.ip_address,
            "endpoint": tgt.ip_address or tgt.hostname or tgt.target_id,
            "provider_label": tgt.provider_label or tgt.hostname or "External Host",
            "packets_transmitted": tx,
            "packets_received": rx,
            "packet_loss_pct": loss_pct,
            "min_latency_ms": min_lat,
            "avg_latency_ms": avg_lat,
            "max_latency_ms": max_lat,
            "median_latency_ms": med_lat,
            "p95_latency_ms": p95_lat,
            "delay_variation_ms": jitter,
            "test_outcome": outcome,
        })

    return {
        "stage_name": st3.stage_name if st3 else "External Multi-Target Ping",
        "status": st_status,
        "targets_count": len(target_results),
        "targets": target_results,
        "error_message": st3.error_message if st3 else None,
    }


def _extract_dns_diagnostics(data: DiagnosticReportData) -> Dict[str, Any]:
    st4 = _find_stage(data.stages, 4, "dns", "domain")
    st_status = st4.status.value if st4 and hasattr(st4.status, "value") else (str(st4.status) if st4 else "NOT_RUN")
    
    dns_obs = [o for o in data.observations if "dns" in o.metric_name.lower() or (st4 and o.stage_id == st4.stage_id)]
    
    queries: List[Dict[str, Any]] = []
    for obs in dns_obs:
        tgt = data.targets.get(obs.target_id) if obs.target_id else None
        domain = (tgt.hostname if tgt else None) or obs.target_id or "default-query"
        resolved_ips: List[str] = []
        if obs.method_metadata:
            try:
                meta = json.loads(obs.method_metadata)
                if isinstance(meta, dict):
                    ips = meta.get("resolved_ips") or meta.get("addresses") or []
                    if isinstance(ips, list):
                        resolved_ips = [str(x) for x in ips]
                    elif isinstance(ips, str):
                        resolved_ips = [ips]
            except Exception:
                pass
        if not resolved_ips and tgt and tgt.ip_address:
            resolved_ips = [tgt.ip_address]
            
        status_val = obs.status.value if hasattr(obs.status, "value") else str(obs.status)
        queries.append({
            "observation_id": obs.observation_id,
            "domain": domain,
            "status": status_val,
            "resolved_ips": resolved_ips,
            "query_time_ms": _safe_float(obs.value_numeric),
            "error_message": obs.error_message,
            "outcome": "SUCCESS" if status_val == "SUCCESS" else "FAILED",
        })

    return {
        "stage_name": st4.stage_name if st4 else "DNS Diagnostics",
        "status": st_status,
        "queries_tested_count": len(queries),
        "queries_tested": queries,
        "error_message": st4.error_message if st4 else None,
    }


def _extract_performance_analysis(data: DiagnosticReportData) -> Dict[str, Any]:
    st5 = _find_stage(data.stages, 5, "throughput", "loaded", "bufferbloat")
    st_status = st5.status.value if st5 and hasattr(st5.status, "value") else (str(st5.status) if st5 else "NOT_RUN")
    
    dl_sum = _find_summary(data.summaries, None, ["download_throughput"])
    baseline_sum = _find_summary(data.summaries, None, ["baseline_rtt", "baseline_latency", "round_trip_time"], ["median", "mean"])
    loaded_sum = _find_summary(data.summaries, None, ["loaded_rtt", "loaded_latency"], ["median", "mean"])
    delta_sum = _find_summary(data.summaries, None, ["loaded_latency_delta", "bufferbloat_delta"])
    
    dl_obs = next((o for o in data.observations if o.metric_name == "download_throughput"), None)
    loaded_obs = next((o for o in data.observations if "loaded" in o.metric_name.lower()), None)
    
    dl_throughput = _safe_float(dl_sum.value_numeric) if dl_sum else (_safe_float(dl_obs.value_numeric) if dl_obs else None)
    baseline_lat = _safe_float(baseline_sum.value_numeric) if baseline_sum else None
    loaded_lat = _safe_float(loaded_sum.value_numeric) if loaded_sum else (_safe_float(loaded_obs.value_numeric) if loaded_obs else None)
    
    delta_lat: Optional[float] = None
    if delta_sum and delta_sum.value_numeric is not None:
        delta_lat = _safe_float(delta_sum.value_numeric)
    elif loaded_lat is not None and baseline_lat is not None:
        delta_lat = round(max(0.0, loaded_lat - baseline_lat), 2)
        
    duration = _calculate_duration_seconds(st5.start_time_utc, st5.end_time_utc) if st5 else None
    bufferbloat = (delta_lat is not None and delta_lat > 50.0)

    return {
        "stage_name": st5.stage_name if st5 else "Throughput & Loaded Latency Analysis",
        "status": st_status,
        "download_throughput_mbps": dl_throughput,
        "upload_throughput_mbps": None,
        "upload_throughput_note": "Not Measured (Unsupported in Unprivileged Diagnostic Scope)",
        "baseline_latency_ms": baseline_lat,
        "loaded_latency_ms": loaded_lat,
        "bufferbloat_delta_ms": delta_lat,
        "bufferbloat_detected": bufferbloat,
        "measurement_duration_seconds": duration,
        "error_message": st5.error_message if st5 else None,
    }


def _extract_diagnostic_findings(data: DiagnosticReportData) -> List[Dict[str, Any]]:
    evidence_by_finding: Dict[str, List[FindingEvidence]] = {}
    for ev in data.evidence:
        evidence_by_finding.setdefault(ev.finding_id, []).append(ev)

    findings_list: List[Dict[str, Any]] = []
    for f in sorted(data.findings, key=lambda x: (x.priority.value if hasattr(x.priority, "value") else str(x.priority), x.finding_id)):
        ev_refs: List[Dict[str, Any]] = []
        for ev in evidence_by_finding.get(f.finding_id, []):
            rel = ev.relationship_type.value if hasattr(ev.relationship_type, "value") else str(ev.relationship_type)
            ev_refs.append({
                "evidence_id": ev.evidence_id,
                "relationship_type": rel,
                "observation_id": ev.observation_id,
                "summary_id": ev.summary_id,
            })
            
        findings_list.append({
            "finding_id": f.finding_id,
            "rule_id": f.rule_id,
            "rule_version": f.rule_version,
            "category": f.category.value if hasattr(f.category, "value") else str(f.category),
            "priority": f.priority.value if hasattr(f.priority, "value") else str(f.priority),
            "status": f.status.value if hasattr(f.status, "value") else str(f.status),
            "evidence_strength": f.evidence_strength.value if hasattr(f.evidence_strength, "value") else str(f.evidence_strength),
            "plain_summary": f.plain_summary,
            "observed_behavior": f.plain_summary,
            "recommended_action": f.recommended_action,
            "limitations": f.limitations,
            "evidence_references": ev_refs,
        })
    return findings_list


def _extract_evidence_and_measurements(data: DiagnosticReportData) -> List[Dict[str, Any]]:
    findings_by_obs: Dict[str, List[str]] = {}
    for ev in data.evidence:
        if ev.observation_id:
            findings_by_obs.setdefault(ev.observation_id, []).append(ev.finding_id)

    evidence_list: List[Dict[str, Any]] = []
    for obs in sorted(data.observations, key=lambda o: (o.sample_index, o.observation_id)):
        tgt = data.targets.get(obs.target_id) if obs.target_id else None
        endpoint = ""
        if tgt:
            endpoint = tgt.ip_address or tgt.hostname or tgt.target_id
        elif obs.target_id:
            endpoint = obs.target_id

        val = (
            obs.value_numeric
            if (obs.value_numeric is not None and math.isfinite(obs.value_numeric))
            else None
        )
        status_val = obs.status.value if hasattr(obs.status, "value") else str(obs.status)
        linked_findings = findings_by_obs.get(obs.observation_id, [])

        evidence_list.append({
            "observation_id": obs.observation_id,
            "sample_index": obs.sample_index,
            "metric_name": obs.metric_name,
            "metric_label": get_metric_human_label(obs.metric_name),
            "value_numeric": val,
            "unit": obs.unit,
            "status": status_val,
            "duration_ms": obs.duration_ms,
            "target_id": obs.target_id,
            "target_endpoint": endpoint,
            "timestamp_utc": obs.timestamp_utc,
            "error_message": obs.error_message,
            "associated_finding_ids": linked_findings,
        })
    return evidence_list


def _extract_warnings_and_limitations(data: DiagnosticReportData) -> Dict[str, List[Dict[str, Any]]]:
    warnings: List[Dict[str, Any]] = []
    limitations: List[Dict[str, Any]] = []

    if data.run.overall_error:
        warnings.append({
            "type": "RUN_WARNING",
            "code": "OVERALL_RUN_ERROR",
            "message": data.run.overall_error,
            "impact": "Diagnostic run completed with non-fatal operational warning",
        })

    for st in data.stages:
        if st.status == StageStatus.FAILED:
            warnings.append({
                "type": "STAGE_FAILURE",
                "code": f"STAGE_{st.stage_order}_FAILED",
                "message": f"Stage '{st.stage_name}' failed: {st.error_message or 'Unspecified error'}",
                "impact": "Downstream diagnostic inferences may be incomplete",
            })
        elif st.status == StageStatus.SKIPPED:
            warnings.append({
                "type": "STAGE_SKIPPED",
                "code": f"STAGE_{st.stage_order}_SKIPPED",
                "message": f"Stage '{st.stage_name}' was skipped: {st.skip_reason or 'Dependency requirement not met'}",
                "impact": "Measurements for this stage were intentionally bypassed",
            })

    timeout_obs = [o for o in data.observations if o.status in (MeasurementStatus.TIMEOUT, MeasurementStatus.FAILED, MeasurementStatus.BLOCKED)]
    if timeout_obs:
        warnings.append({
            "type": "OBSERVATION_TIMEOUTS",
            "code": "TIMEOUT_COUNT",
            "message": f"{len(timeout_obs)} measurement probes timed out or encountered transmission errors",
            "impact": "Probed destinations were unresponsive within the configured measurement window",
        })

    for f in data.findings:
        if f.category == FindingCategory.INCONCLUSIVE or f.evidence_strength == EvidenceStrength.INCONCLUSIVE:
            limitations.append({
                "type": "INCONCLUSIVE_DIAGNOSIS",
                "rule_id": f.rule_id,
                "summary": f.plain_summary,
                "limitation": f.limitations,
            })

    limitations.append({
        "type": "METHODOLOGICAL_GUARDRAIL",
        "name": "Single-Target Outage Guardrail",
        "description": "Failures observed on a single external host do not confirm an upstream ISP outage. Confirmed external path failure requires simultaneous multi-target corroboration.",
    })
    limitations.append({
        "type": "METHODOLOGICAL_GUARDRAIL",
        "name": "Unprivileged Execution Scope",
        "description": "TicNet runs in standard user space without requiring administrator elevation. Low-level physical Wi-Fi radio metrics and hardware link counters cannot be accessed.",
    })
    limitations.append({
        "type": "METHODOLOGICAL_GUARDRAIL",
        "name": "Missing Measurement Honesty",
        "description": "Missing, failed, or timed-out measurements are strictly preserved as null/unavailable and are never coerced to zero.",
    })

    return {
        "warnings": warnings,
        "limitations": limitations,
    }


def serialize_to_json(
    data: DiagnosticReportData,
    privacy_filter: Optional[PrivacyFilter] = None,
    indent: int = 2,
) -> str:
    """Serialize complete diagnostic report to deterministic, human-readable JSON."""
    run_summary = _extract_run_summary(data)
    network_env = _extract_network_environment(data)
    gw_discovery = _extract_gateway_discovery(data)
    gw_reachability = _extract_gateway_reachability(data)
    multi_ping = _extract_multi_target_ping(data)
    dns_diag = _extract_dns_diagnostics(data)
    perf_analysis = _extract_performance_analysis(data)
    findings_list = _extract_diagnostic_findings(data)
    evidence_list = _extract_evidence_and_measurements(data)
    warn_lim = _extract_warnings_and_limitations(data)

    # Legacy payloads for backward-compatibility with tests expecting root targets/stages/observations/summaries/findings:
    evidence_by_finding: Dict[str, List[Dict[str, Any]]] = {}
    for ev in data.evidence:
        ev_dict = {
            "evidence_id": ev.evidence_id,
            "finding_id": ev.finding_id,
            "relationship_type": (
                ev.relationship_type.value
                if hasattr(ev.relationship_type, "value")
                else str(ev.relationship_type)
            ),
            "observation_id": ev.observation_id,
            "summary_id": ev.summary_id,
        }
        evidence_by_finding.setdefault(ev.finding_id, []).append(ev_dict)

    targets_payload = []
    for target in sorted(data.targets.values(), key=lambda t: t.target_id):
        targets_payload.append({
            "target_id": target.target_id,
            "target_type": (
                target.target_type.value
                if hasattr(target.target_type, "value")
                else str(target.target_type)
            ),
            "hostname": target.hostname,
            "ip_address": target.ip_address,
            "port": target.port,
            "provider_label": target.provider_label,
            "is_active": target.is_active,
        })

    stages_payload = []
    for stage in sorted(data.stages, key=lambda s: s.stage_order):
        stages_payload.append({
            "stage_id": stage.stage_id,
            "stage_name": stage.stage_name,
            "stage_order": stage.stage_order,
            "status": (
                stage.status.value
                if hasattr(stage.status, "value")
                else str(stage.status)
            ),
            "start_time_utc": stage.start_time_utc,
            "end_time_utc": stage.end_time_utc,
            "skip_reason": stage.skip_reason,
            "error_message": stage.error_message,
        })

    observations_payload = []
    for obs in sorted(data.observations, key=lambda o: (o.sample_index, o.observation_id)):
        observations_payload.append({
            "observation_id": obs.observation_id,
            "stage_id": obs.stage_id,
            "target_id": obs.target_id,
            "sample_index": obs.sample_index,
            "timestamp_utc": obs.timestamp_utc,
            "metric_name": obs.metric_name,
            "unit": obs.unit,
            "status": (
                obs.status.value
                if hasattr(obs.status, "value")
                else str(obs.status)
            ),
            "value_numeric": (
                obs.value_numeric
                if (obs.value_numeric is not None and math.isfinite(obs.value_numeric))
                else None
            ),
            "duration_ms": obs.duration_ms,
            "error_message": obs.error_message,
            "method_metadata": obs.method_metadata,
        })

    summaries_payload = []
    for summary in sorted(data.summaries, key=lambda s: (s.metric_name, s.statistic_name)):
        summaries_payload.append({
            "summary_id": summary.summary_id,
            "target_id": summary.target_id,
            "metric_name": summary.metric_name,
            "statistic_name": summary.statistic_name,
            "unit": summary.unit,
            "value_numeric": (
                summary.value_numeric
                if (summary.value_numeric is not None and math.isfinite(summary.value_numeric))
                else None
            ),
            "sample_count_total": summary.sample_count_total,
            "sample_count_valid": summary.sample_count_valid,
            "calculation_method": summary.calculation_method,
            "calculation_version": summary.calculation_version,
        })

    legacy_findings_payload = []
    for finding in sorted(data.findings, key=lambda f: (f.priority.value if hasattr(f.priority, "value") else str(f.priority), f.finding_id)):
        legacy_findings_payload.append({
            "finding_id": finding.finding_id,
            "category": (
                finding.category.value
                if hasattr(finding.category, "value")
                else str(finding.category)
            ),
            "priority": (
                finding.priority.value
                if hasattr(finding.priority, "value")
                else str(finding.priority)
            ),
            "rule_id": finding.rule_id,
            "rule_version": finding.rule_version,
            "status": (
                finding.status.value
                if hasattr(finding.status, "value")
                else str(finding.status)
            ),
            "evidence_strength": (
                finding.evidence_strength.value
                if hasattr(finding.evidence_strength, "value")
                else str(finding.evidence_strength)
            ),
            "plain_summary": finding.plain_summary,
            "limitations": finding.limitations,
            "recommended_action": finding.recommended_action,
            "evidence": evidence_by_finding.get(finding.finding_id, []),
        })

    metadata_dict = {
        "run_id": data.run.run_id,
        "product_name": "TicNet",
        "product_version": data.run.app_version or "1.0.0",
        "report_title": "TicNet Comprehensive Diagnostic Report",
        "generated_at_utc": data.generated_at_utc,
        "execution_timestamp_utc": data.run.start_time_utc,
        "overall_status": data.run.status.value if hasattr(data.run.status, "value") else str(data.run.status),
        "app_version": data.run.app_version,
        "config_version": data.run.config_version,
        "run_mode": (
            data.run.run_mode.value
            if hasattr(data.run.run_mode, "value")
            else str(data.run.run_mode)
        ),
        "status": (
            data.run.status.value
            if hasattr(data.run.status, "value")
            else str(data.run.status)
        ),
        "start_time_utc": data.run.start_time_utc,
        "end_time_utc": data.run.end_time_utc,
        "interface_name": data.run.interface_name,
        "interface_type": data.run.interface_type,
        "gateway_ip": data.run.gateway_ip,
        "overall_error": data.run.overall_error,
    }

    report_dict = {
        "schema_version": data.schema_version,
        "report_metadata": metadata_dict,
        "diagnostic_run": run_summary,
        "network_environment": network_env,
        "gateway_discovery": gw_discovery,
        "gateway_reachability": gw_reachability,
        "multi_target_ping": multi_ping,
        "dns_diagnostics": dns_diag,
        "performance_analysis": perf_analysis,
        "diagnostic_findings": findings_list,
        "evidence": evidence_list,
        "warnings": warn_lim["warnings"],
        "limitations": warn_lim["limitations"],
        # Top-level legacy arrays for backwards-compatibility:
        "targets": targets_payload,
        "stages": stages_payload,
        "observations": observations_payload,
        "summaries": summaries_payload,
        "findings": legacy_findings_payload,
    }

    if privacy_filter is not None:
        report_dict = privacy_filter.apply_to_obj(report_dict)

    return json.dumps(report_dict, indent=indent, allow_nan=False)


def serialize_report_to_csv(
    data: DiagnosticReportData,
    privacy_filter: Optional[PrivacyFilter] = None,
) -> str:
    """Serialize diagnostic report to complete, human-readable 10-section tabular CSV."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)

    def write_row(cells: List[Any]) -> None:
        sanitized = []
        for c in cells:
            if privacy_filter and isinstance(c, str):
                c = privacy_filter.apply_to_text(c)
            sanitized.append(sanitize_csv_cell(c))
        writer.writerow(sanitized)

    def write_banner(title: str) -> None:
        writer.writerow([f"# =============================================================================="])
        writer.writerow([f"# {title}"])
        writer.writerow([f"# =============================================================================="])

    overview = _extract_report_overview(data)
    run_sum = _extract_run_summary(data)
    gw_disc = _extract_gateway_discovery(data)
    gw_reach = _extract_gateway_reachability(data)
    multi_ping = _extract_multi_target_ping(data)
    dns_diag = _extract_dns_diagnostics(data)
    perf = _extract_performance_analysis(data)
    findings = _extract_diagnostic_findings(data)
    evidence_items = _extract_evidence_and_measurements(data)
    warn_lim = _extract_warnings_and_limitations(data)

    # -------------------------------------------------------------------------
    # Section 1: Report Overview
    # -------------------------------------------------------------------------
    write_banner("SECTION 1: REPORT OVERVIEW")
    writer.writerow(["Section", "Property", "Value", "Unit", "Description / Notes"])
    write_row(["Report Overview", "Product Name", overview["product_name"], "", "Native Windows Network Diagnostics Platform"])
    write_row(["Report Overview", "Product Version", overview["product_version"], "", "Desktop Production Release"])
    write_row(["Report Overview", "Report Title", overview["report_title"], "", "Deterministic Connectivity Assessment"])
    write_row(["Report Overview", "Diagnostic Run ID", overview["run_id"], "", "Unique Diagnostic Session Identifier"])
    write_row(["Report Overview", "Report Generated (IST)", overview["report_generated_utc"], "", "Timestamp When Report Was Exported"])
    write_row(["Report Overview", "Diagnostic Execution Start (IST)", overview["execution_start_utc"], "", "Timestamp When Pipeline Started"])
    write_row(["Report Overview", "Diagnostic Execution End (IST)", overview["execution_end_utc"] or "In-Progress / Interrupted", "", "Timestamp When Pipeline Completed"])
    write_row(["Report Overview", "Overall Diagnostic Status", overview["overall_diagnostic_status"], "", "Pipeline Assessment Outcome"])
    writer.writerow([])

    # -------------------------------------------------------------------------
    # Section 2: Diagnostic Run Summary
    # -------------------------------------------------------------------------
    write_banner("SECTION 2: DIAGNOSTIC RUN SUMMARY")
    writer.writerow(["Section", "Metric / Parameter", "Measured Value", "Unit", "Assessment / Details"])
    write_row(["Diagnostic Run Summary", "Total Execution Duration", run_sum["duration_seconds"] if run_sum["duration_seconds"] is not None else "Not Available", "seconds", "Total elapsed measurement time"])
    write_row(["Diagnostic Run Summary", "Active Network Interface Name", run_sum["interface_name"], "", "Host network adapter used during diagnosis"])
    write_row(["Diagnostic Run Summary", "Active Network Interface Type", run_sum["interface_type"], "", "Physical connection medium"])
    write_row(["Diagnostic Run Summary", "Total Diagnostic Stages", run_sum["stages_total"], "stages", "Scheduled measurement stages in pipeline"])
    write_row(["Diagnostic Run Summary", "Successful Stages", run_sum["stages_successful"], "stages", "Stages evaluated without fatal faults"])
    write_row(["Diagnostic Run Summary", "Failed Stages", run_sum["stages_failed"], "stages", "Stages halted due to connectivity or system faults"])
    write_row(["Diagnostic Run Summary", "Skipped Stages", run_sum["stages_skipped"], "stages", "Stages bypassed due to dependency requirements"])
    write_row(["Diagnostic Run Summary", "Inconclusive Stages", run_sum["stages_inconclusive"], "stages", "Stages with partial or uncorroborated evidence"])
    writer.writerow([])

    # -------------------------------------------------------------------------
    # Section 3: Gateway Discovery
    # -------------------------------------------------------------------------
    write_banner("SECTION 3: GATEWAY DISCOVERY (LOCAL ROUTER DETECTION)")
    writer.writerow(["Section", "Parameter", "Discovered Value", "Discovery Source / Method", "Status / Notes"])
    write_row(["Gateway Discovery", "Discovery Stage Status", gw_disc["status"], "", "Outcome of default gateway detection"])
    write_row(["Gateway Discovery", "Default Gateway IP Address", gw_disc["gateway_ip"] or "Not Available", gw_disc["discovery_method"], "Local router address"])
    write_row(["Gateway Discovery", "Discovery Timestamp (IST)", gw_disc["timestamp_utc"], "", "Time of route table inspection"])
    write_row(["Gateway Discovery", "Discovery Evidence", gw_disc["evidence_summary"], "", "Route inspection details"])
    write_row(["Gateway Discovery", "Discovery Error Details", gw_disc["error_message"] or "None", "", "Recorded errors if any"])
    writer.writerow([])

    # -------------------------------------------------------------------------
    # Section 4: Gateway Reachability
    # -------------------------------------------------------------------------
    write_banner("SECTION 4: GATEWAY REACHABILITY (HOP 1 / LOCAL NETWORK PATH)")
    writer.writerow([
        "Section",
        "Target Gateway",
        "Packets Transmitted",
        "Packets Received",
        "Packet Loss (%)",
        "Min RTT (ms)",
        "Average RTT (ms)",
        "Max RTT (ms)",
        "Median RTT (ms)",
        "P95 RTT (ms)",
        "Delay Variation / Jitter (ms)",
        "Test Duration (s)",
        "Test Status",
    ])
    write_row([
        "Gateway Reachability",
        gw_reach["target_gateway_ip"],
        gw_reach["packets_transmitted"],
        gw_reach["packets_received"],
        gw_reach["packet_loss_pct"] if gw_reach["packet_loss_pct"] is not None else "",
        gw_reach["min_rtt_ms"] if gw_reach["min_rtt_ms"] is not None else "",
        gw_reach["avg_rtt_ms"] if gw_reach["avg_rtt_ms"] is not None else "",
        gw_reach["max_rtt_ms"] if gw_reach["max_rtt_ms"] is not None else "",
        gw_reach["median_rtt_ms"] if gw_reach["median_rtt_ms"] is not None else "",
        gw_reach["p95_rtt_ms"] if gw_reach["p95_rtt_ms"] is not None else "",
        gw_reach["delay_variation_ms"] if gw_reach["delay_variation_ms"] is not None else "",
        gw_reach["test_duration_seconds"] if gw_reach["test_duration_seconds"] is not None else "",
        gw_reach["status"],
    ])
    writer.writerow([])

    # -------------------------------------------------------------------------
    # Section 5: Multi-Target External Ping Analysis
    # -------------------------------------------------------------------------
    write_banner("SECTION 5: MULTI-TARGET EXTERNAL PING ANALYSIS (HOP 2 / UPSTREAM TRANSIT)")
    writer.writerow([
        "Section",
        "Target Hostname / IP",
        "Provider / Description",
        "Packets Transmitted",
        "Packets Received",
        "Packet Loss (%)",
        "Min Latency (ms)",
        "Average Latency (ms)",
        "Max Latency (ms)",
        "Median Latency (ms)",
        "P95 Latency (ms)",
        "Delay Variation / Jitter (ms)",
        "Test Outcome",
    ])
    if multi_ping["targets"]:
        for t in multi_ping["targets"]:
            write_row([
                "Multi-Target Ping",
                t["endpoint"],
                t["provider_label"],
                t["packets_transmitted"],
                t["packets_received"],
                t["packet_loss_pct"] if t["packet_loss_pct"] is not None else "",
                t["min_latency_ms"] if t["min_latency_ms"] is not None else "",
                t["avg_latency_ms"] if t["avg_latency_ms"] is not None else "",
                t["max_latency_ms"] if t["max_latency_ms"] is not None else "",
                t["median_latency_ms"] if t["median_latency_ms"] is not None else "",
                t["p95_latency_ms"] if t["p95_latency_ms"] is not None else "",
                t["delay_variation_ms"] if t["delay_variation_ms"] is not None else "",
                t["test_outcome"],
            ])
    else:
        write_row(["Multi-Target Ping", "No external destinations probed", "", "", "", "", "", "", "", "", "", "", "SKIPPED"])
    writer.writerow([])

    # -------------------------------------------------------------------------
    # Section 6: DNS Diagnostics
    # -------------------------------------------------------------------------
    write_banner("SECTION 6: DNS DIAGNOSTICS (NAME RESOLUTION SUBSYSTEM)")
    writer.writerow([
        "Section",
        "Queried Domain",
        "Resolution Status",
        "Resolved IP Address(es)",
        "Response Time (ms)",
        "Test Outcome",
        "Error / Diagnostics",
    ])
    if dns_diag["queries_tested"]:
        for q in dns_diag["queries_tested"]:
            resolved_str = " / ".join(q["resolved_ips"]) if q["resolved_ips"] else "None"
            write_row([
                "DNS Diagnostics",
                q["domain"],
                q["status"],
                resolved_str,
                q["query_time_ms"] if q["query_time_ms"] is not None else "",
                q["outcome"],
                q["error_message"] or "Query resolved successfully",
            ])
    else:
        write_row(["DNS Diagnostics", "No DNS queries recorded", "SKIPPED", "", "", "SKIPPED", "Stage not executed or no domain targets"])
    writer.writerow([])

    # -------------------------------------------------------------------------
    # Section 7: Throughput and Performance Analysis
    # -------------------------------------------------------------------------
    write_banner("SECTION 7: THROUGHPUT AND PERFORMANCE ANALYSIS (LOADED LATENCY & BUFFERBLOAT)")
    writer.writerow([
        "Section",
        "Performance Metric",
        "Measured Value",
        "Measurement Unit",
        "Baseline / Reference",
        "Test Status",
        "Observations / Assessment",
    ])
    write_row([
        "Throughput & Performance",
        "Download Throughput",
        perf["download_throughput_mbps"] if perf["download_throughput_mbps"] is not None else "Not Measured",
        "Mbps",
        "Target Downstream Transit",
        perf["status"],
        "Bounded unprivileged downstream load test",
    ])
    write_row([
        "Throughput & Performance",
        "Upload Throughput",
        "Not Measured",
        "Mbps",
        "N/A",
        "SKIPPED",
        "Unprivileged home diagnostic scope excludes upstream saturation",
    ])
    write_row([
        "Throughput & Performance",
        "Baseline Latency (Unloaded)",
        perf["baseline_latency_ms"] if perf["baseline_latency_ms"] is not None else "Not Measured",
        "ms",
        "Idle Network State",
        perf["status"],
        "Median round-trip latency prior to downstream load",
    ])
    write_row([
        "Throughput & Performance",
        "Loaded Latency (Saturated)",
        perf["loaded_latency_ms"] if perf["loaded_latency_ms"] is not None else "Not Measured",
        "ms",
        "Active Load Window",
        perf["status"],
        "Median round-trip latency during downstream load",
    ])
    bb_desc = (
        "Significant bufferbloat latency detected (>50ms delta)"
        if perf["bufferbloat_detected"]
        else "Nominal latency increase under load"
    )
    write_row([
        "Throughput & Performance",
        "Bufferbloat Latency Increase (Delta)",
        perf["bufferbloat_delta_ms"] if perf["bufferbloat_delta_ms"] is not None else "Not Measured",
        "ms",
        "Loaded - Baseline",
        perf["status"],
        bb_desc,
    ])
    write_row([
        "Throughput & Performance",
        "Measurement Duration",
        perf["measurement_duration_seconds"] if perf["measurement_duration_seconds"] is not None else "Not Measured",
        "seconds",
        "Active Load Window",
        perf["status"],
        "Duration of throughput measurement window",
    ])
    writer.writerow([])

    # -------------------------------------------------------------------------
    # Section 8: Diagnostic Findings
    # -------------------------------------------------------------------------
    write_banner("SECTION 8: DIAGNOSTIC FINDINGS AND RULE EVALUATIONS")
    writer.writerow([
        "Section",
        "Finding ID",
        "Rule ID",
        "Finding Category",
        "Severity / Priority",
        "Diagnostic Status",
        "Evidence Strength",
        "Plain English Summary",
        "Observed Behavior",
        "Recommended Action",
        "Methodological Limitations",
    ])
    if findings:
        for f in findings:
            write_row([
                "Diagnostic Findings",
                f["finding_id"],
                f["rule_id"],
                f["category"],
                f["priority"],
                f["status"],
                f["evidence_strength"],
                f["plain_summary"],
                f["observed_behavior"],
                f["recommended_action"],
                f["limitations"],
            ])
    else:
        write_row([
            "Diagnostic Findings",
            "",
            "",
            "All Categories",
            "INFO",
            "NOMINAL",
            "STRONG",
            "No connectivity faults or network anomalies detected.",
            "All empirical measurements satisfied nominal network thresholds.",
            "No corrective actions required.",
            "Physical Wi-Fi radio counters unavailable without administrative elevation.",
        ])
    writer.writerow([])

    # -------------------------------------------------------------------------
    # Section 9: Empirical Evidence and Measurements
    # -------------------------------------------------------------------------
    write_banner("SECTION 9: EMPIRICAL EVIDENCE AND RAW PROBE MEASUREMENTS")
    writer.writerow([
        "Section",
        "Sample #",
        "Observation ID",
        "Metric Name",
        "Human Label",
        "Observed Value",
        "Measurement Unit",
        "Measurement Status",
        "Duration (ms)",
        "Target Endpoint",
        "Timestamp (IST)",
        "Associated Finding(s)",
        "Error Details",
    ])
    if evidence_items:
        for ev in evidence_items:
            findings_str = ", ".join(ev["associated_finding_ids"])
            val = ev["value_numeric"] if ev["value_numeric"] is not None else ""
            write_row([
                "Evidence & Measurements",
                ev["sample_index"],
                ev["observation_id"],
                ev["metric_name"],
                ev["metric_label"],
                val,
                ev["unit"],
                ev["status"],
                ev["duration_ms"],
                ev["target_endpoint"],
                ev["timestamp_utc"],
                findings_str,
                ev["error_message"] or "",
            ])
    else:
        write_row([
            "Evidence & Measurements",
            "",
            "",
            "",
            "No raw probe observations recorded",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ])
    writer.writerow([])

    # -------------------------------------------------------------------------
    # Section 10: Warnings, Limitations, and Methodological Guardrails
    # -------------------------------------------------------------------------
    write_banner("SECTION 10: WARNINGS, LIMITATIONS, AND METHODOLOGICAL GUARDRAILS")
    writer.writerow([
        "Section",
        "Item Type",
        "Category / Code",
        "Status / Level",
        "Description / Details",
        "Impact on Diagnostic Conclusions",
    ])
    for w in warn_lim["warnings"]:
        write_row([
            "Warnings & Limitations",
            "Warning",
            w["code"],
            "WARNING",
            w["message"],
            w["impact"],
        ])
    for lim in warn_lim["limitations"]:
        item_type = lim.get("type", "LIMITATION")
        name = lim.get("name") or lim.get("rule_id") or "Architectural Guardrail"
        desc = lim.get("description") or lim.get("limitation") or lim.get("summary") or ""
        impact = lim.get("summary") or "Prevents false-positive diagnostic conclusions"
        write_row([
            "Warnings & Limitations",
            item_type,
            name,
            "ACTIVE",
            desc,
            impact,
        ])

    return output.getvalue()


CSV_OBSERVATION_COLUMNS = [
    "observation_id",
    "run_id",
    "stage_id",
    "sample_index",
    "timestamp_utc",
    "target_id",
    "target_type",
    "target_endpoint",
    "metric_name",
    "value_numeric",
    "unit",
    "status",
    "duration_ms",
    "error_message",
]


def _serialize_raw_observations_to_csv(
    observations: List[Observation],
    targets: Optional[Dict[str, Target]] = None,
    privacy_filter: Optional[PrivacyFilter] = None,
) -> str:
    """Serialize raw probe observations to tabular CSV (legacy backwards-compatibility)."""
    targets = targets or {}
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)

    writer.writerow(CSV_OBSERVATION_COLUMNS)
    sorted_obs = sorted(observations, key=lambda o: (o.sample_index, o.observation_id))

    for obs in sorted_obs:
        target = targets.get(obs.target_id) if obs.target_id else None
        target_type_str = ""
        target_endpoint_str = ""
        if target:
            target_type_str = (
                target.target_type.value
                if hasattr(target.target_type, "value")
                else str(target.target_type)
            )
            target_endpoint_str = target.ip_address or target.hostname or ""

        error_msg = obs.error_message or ""

        if privacy_filter:
            target_endpoint_str = privacy_filter.apply_to_text(target_endpoint_str)
            error_msg = privacy_filter.apply_to_text(error_msg)

        status_str = (
            obs.status.value if hasattr(obs.status, "value") else str(obs.status)
        )

        obs_val = (
            obs.value_numeric
            if (obs.value_numeric is not None and math.isfinite(obs.value_numeric))
            else ""
        )

        row = [
            sanitize_csv_cell(obs.observation_id),
            sanitize_csv_cell(obs.run_id),
            sanitize_csv_cell(obs.stage_id or ""),
            obs.sample_index,
            sanitize_csv_cell(obs.timestamp_utc),
            sanitize_csv_cell(obs.target_id or ""),
            sanitize_csv_cell(target_type_str),
            sanitize_csv_cell(target_endpoint_str),
            sanitize_csv_cell(obs.metric_name),
            obs_val,
            sanitize_csv_cell(obs.unit),
            sanitize_csv_cell(status_str),
            obs.duration_ms,
            sanitize_csv_cell(error_msg),
        ]
        writer.writerow(row)

    return output.getvalue()


def serialize_to_csv(
    data_or_observations: Union[DiagnosticReportData, List[Observation]],
    targets: Optional[Dict[str, Target]] = None,
    privacy_filter: Optional[PrivacyFilter] = None,
) -> str:
    """Serialize diagnostic report or raw observations to tabular CSV.
    
    If data_or_observations is DiagnosticReportData:
        Serializes the comprehensive 10-section professional diagnostic report.
    If data_or_observations is a List[Observation]:
        Serializes raw probe observations adhering to CSV_OBSERVATION_COLUMNS (backwards-compatibility).
    """
    if isinstance(data_or_observations, DiagnosticReportData):
        return serialize_report_to_csv(data_or_observations, privacy_filter=privacy_filter)
    return _serialize_raw_observations_to_csv(
        observations=data_or_observations,
        targets=targets,
        privacy_filter=privacy_filter,
    )


CSV_SUMMARY_COLUMNS = [
    "summary_id",
    "run_id",
    "target_id",
    "metric_name",
    "statistic_name",
    "value_numeric",
    "unit",
    "sample_count_total",
    "sample_count_valid",
    "calculation_method",
    "calculation_version",
]


def serialize_summaries_to_csv(
    summaries: List[Summary],
    privacy_filter: Optional[PrivacyFilter] = None,
) -> str:
    """Serialize statistical summaries to tabular CSV with formula sanitization."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)

    writer.writerow(CSV_SUMMARY_COLUMNS)
    sorted_summaries = sorted(summaries, key=lambda s: (s.metric_name, s.statistic_name))

    for s in sorted_summaries:
        target_id_str = s.target_id or ""
        if privacy_filter:
            target_id_str = privacy_filter.apply_to_text(target_id_str)

        sum_val = (
            s.value_numeric
            if (s.value_numeric is not None and math.isfinite(s.value_numeric))
            else ""
        )

        row = [
            sanitize_csv_cell(s.summary_id),
            sanitize_csv_cell(s.run_id),
            sanitize_csv_cell(target_id_str),
            sanitize_csv_cell(s.metric_name),
            sanitize_csv_cell(s.statistic_name),
            sum_val,
            sanitize_csv_cell(s.unit),
            s.sample_count_total,
            s.sample_count_valid,
            sanitize_csv_cell(s.calculation_method),
            sanitize_csv_cell(s.calculation_version),
        ]
        writer.writerow(row)

    return output.getvalue()


def serialize_to_markdown(
    data: DiagnosticReportData,
    privacy_filter: Optional[PrivacyFilter] = None,
) -> str:
    """Generate human-readable technician markdown summary suitable for ISP tickets (AC-PERS-04)."""
    run = data.run
    gateway_ip = run.gateway_ip or "N/A"
    if privacy_filter:
        gateway_ip = privacy_filter.apply_to_text(gateway_ip)

    lines: List[str] = [
        "# TicNet Diagnostic Report & Technician Summary",
        "",
        "## 1. Diagnostic Execution Overview",
        "",
        f"- **Product Name:** TicNet v{run.app_version or '1.0.0'} (Config: `{run.config_version}`)",
        f"- **Run ID:** `{run.run_id}`",
        f"- **Diagnostic Status:** **{run.status.value if hasattr(run.status, 'value') else str(run.status)}**",
        f"- **Execution Mode:** {run.run_mode.value if hasattr(run.run_mode, 'value') else str(run.run_mode)}",
        f"- **Report Generated (IST):** {format_ist(data.generated_at_utc)}",
        f"- **Run Start Time (IST):** {format_ist(run.start_time_utc)}",
        f"- **Run End Time (IST):** {format_ist(run.end_time_utc) if run.end_time_utc else 'In-Progress / Interrupted'}",
        f"- **Active Network Interface:** `{run.interface_name or 'N/A'}` ({run.interface_type or 'N/A'})",
        f"- **Default Gateway IP:** `{gateway_ip}`",
    ]

    if run.overall_error:
        err_msg = (
            privacy_filter.apply_to_text(run.overall_error)
            if privacy_filter
            else run.overall_error
        )
        lines.extend(["", f"> [!WARNING]", f"> **Run Warning / Error:** {err_msg}"])

    lines.extend([
        "",
        "## 2. Diagnostic Findings & Rule Outcomes",
        "",
    ])

    if not data.findings:
        lines.append("*No diagnostic findings recorded for this run.*")
    else:
        lines.extend([
            "| Priority | Category | Rule ID | Outcome | Evidence Strength | Summary |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ])
        for f in data.findings:
            priority_val = f.priority.value if hasattr(f.priority, "value") else str(f.priority)
            cat_val = f.category.value if hasattr(f.category, "value") else str(f.category)
            status_val = f.status.value if hasattr(f.status, "value") else str(f.status)
            strength_val = f.evidence_strength.value if hasattr(f.evidence_strength, "value") else str(f.evidence_strength)
            plain_summary = (
                privacy_filter.apply_to_text(f.plain_summary)
                if privacy_filter
                else f.plain_summary
            )
            clean_summary = plain_summary.replace("|", "/")
            lines.append(
                f"| `{priority_val}` | {cat_val} | `{f.rule_id}` | **{status_val}** | {strength_val} | {clean_summary} |"
            )

        lines.extend(["", "### Detailed Finding Analyses", ""])

        evidence_by_finding: Dict[str, List[FindingEvidence]] = {}
        for ev in data.evidence:
            evidence_by_finding.setdefault(ev.finding_id, []).append(ev)

        for idx, f in enumerate(data.findings, 1):
            summary_txt = (
                privacy_filter.apply_to_text(f.plain_summary)
                if privacy_filter
                else f.plain_summary
            )
            limits_txt = (
                privacy_filter.apply_to_text(f.limitations)
                if privacy_filter
                else f.limitations
            )
            action_txt = (
                privacy_filter.apply_to_text(f.recommended_action)
                if privacy_filter
                else f.recommended_action
            )

            status_val = f.status.value if hasattr(f.status, "value") else str(f.status)
            strength_val = f.evidence_strength.value if hasattr(f.evidence_strength, "value") else str(f.evidence_strength)

            lines.extend([
                f"#### Finding {idx}: [{f.rule_id}] {f.category.value if hasattr(f.category, 'value') else str(f.category)}",
                "",
                f"- **Status:** {status_val} (Strength: {strength_val})",
                f"- **Rule Version:** `{f.rule_version}`",
                f"- **Summary:** {summary_txt}",
                f"- **Limitations:** {limits_txt}",
                f"- **Actionable Recommendation:** {action_txt}",
            ])

            f_evidence = evidence_by_finding.get(f.finding_id, [])
            if f_evidence:
                lines.append("- **Supporting / Conflicting Evidence Links:**")
                for ev in f_evidence:
                    rel_type = (
                        ev.relationship_type.value
                        if hasattr(ev.relationship_type, "value")
                        else str(ev.relationship_type)
                    )
                    ref = f"Observation `{ev.observation_id}`" if ev.observation_id else f"Summary `{ev.summary_id}`"
                    lines.append(f"  - [{rel_type}] {ref}")
            lines.append("")

    lines.extend([
        "## 3. Key Telemetry & Statistical Summaries",
        "",
    ])

    if not data.summaries:
        lines.append("*No statistical metric summaries computed.*")
    else:
        lines.extend([
            "| Metric | Statistic | Target Endpoint | Value | Valid / Total Samples | Method |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ])
        for s in data.summaries:
            target = data.targets.get(s.target_id) if s.target_id else None
            endpoint_str = ""
            if target:
                endpoint_str = target.ip_address or target.hostname or target.target_id
                if privacy_filter:
                    endpoint_str = privacy_filter.apply_to_text(endpoint_str)
            else:
                endpoint_str = s.target_id or "Global"

            val_str = f"{s.value_numeric:.2f} {s.unit}" if s.value_numeric is not None else "NULL (No Valid Samples)"
            lines.append(
                f"| `{s.metric_name}` | {s.statistic_name} | `{endpoint_str}` | **{val_str}** | {s.sample_count_valid} / {s.sample_count_total} | {s.calculation_method} |"
            )

    lines.extend([
        "",
        "## 4. Methodological Limitations and Guardrails",
        "",
        "- **Single-Target Guardrail:** Failures observed on a single external host do not confirm an upstream ISP outage. Confirmed external path failure requires simultaneous multi-target corroboration.",
        "- **Radio Telemetry Guardrail:** Wi-Fi interference or physical layer cable degradation cannot be definitively claimed without local hardware radio metrics.",
        "- **Missing Measurement Semantics:** Metric values shown as `NULL` denote measurement timeouts, blocked ports, or insufficient data, strictly distinguished from measured `0.0`.",
        "",
        "---",
        "*TicNet Explainable Diagnostic Exporter — Safe & Traceable Network Diagnostics*",
        "",
    ])

    return "\n".join(lines)
