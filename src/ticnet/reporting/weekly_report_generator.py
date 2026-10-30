"""Weekly Historical Report Generator for TicNet.

Generates comprehensive weekly performance and incident reports in PDF and CSV formats
from historical network monitoring records with complete provenance tagging:
- Prominently marks simulated historical records:
  "SIMULATED HISTORICAL DATA — NOT ACTUAL LIVE NETWORK MEASUREMENTS"
- Supports the 4 calendar weeks in Indian Standard Time (IST):
  - Week 1: 10–11 October 2026 (Partial week)
  - Week 2: 12–18 October 2026 (Full week)
  - Week 3: 19–25 October 2026 (Full week)
  - Week 4: 26–29 October 2026 (Partial week)
  - Full Simulation: 10–29 October 2026
- Generates beautiful, styled PDF reports using QPdfWriter and QTextDocument (zero external PDF deps).
- Generates RFC 4180 CSV exports with formula-injection defenses (prefixing '=', '+', '-', '@' with "'").
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import io
import os
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtGui import QFont, QPdfWriter, QTextDocument

from ticnet.core.timezone import IST, format_ist, to_ist
from ticnet.persistence.database import DatabaseManager
from ticnet.utils.logging import get_logger

logger = get_logger("reporting.weekly_report_generator")


@dataclass(frozen=True)
class WeeklyPeriod:
    """Definition of an IST reporting period."""

    period_key: str
    name: str
    start_date_ist: str  # YYYY-MM-DD
    end_date_ist: str    # YYYY-MM-DD
    is_partial: bool
    description: str


WEEKLY_PERIODS: Dict[str, WeeklyPeriod] = {
    "week_1": WeeklyPeriod(
        period_key="week_1",
        name="Week 1 (10–11 Oct 2026)",
        start_date_ist="2026-10-10",
        end_date_ist="2026-10-11",
        is_partial=True,
        description="Partial initial week establishing historical telemetry baseline.",
    ),
    "week_2": WeeklyPeriod(
        period_key="week_2",
        name="Week 2 (12–18 Oct 2026)",
        start_date_ist="2026-10-12",
        end_date_ist="2026-10-18",
        is_partial=False,
        description="Full calendar week featuring Fault A (Latency Degradation) and Fault B (Jitter Instability).",
    ),
    "week_3": WeeklyPeriod(
        period_key="week_3",
        name="Week 3 (19–25 Oct 2026)",
        start_date_ist="2026-10-19",
        end_date_ist="2026-10-25",
        is_partial=False,
        description="Full calendar week featuring Fault C (Packet Loss Storm) and Fault D (Downstream Throughput Saturation).",
    ),
    "week_4": WeeklyPeriod(
        period_key="week_4",
        name="Week 4 (26–29 Oct 2026)",
        start_date_ist="2026-10-26",
        end_date_ist="2026-10-29",
        is_partial=True,
        description="Partial final week featuring Fault E (Multi-Metric Storm) and Stage 3 recovery verification.",
    ),
    "full_sim": WeeklyPeriod(
        period_key="full_sim",
        name="Full Simulation Period (10–29 Oct 2026)",
        start_date_ist="2026-10-10",
        end_date_ist="2026-10-29",
        is_partial=False,
        description="Complete 20-day historical monitoring dataset with 587 simulated runs and 8 tracked incidents.",
    ),
}


class WeeklyReportGenerator:
    """Generates weekly performance and incident reports in PDF and CSV formats."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_period_data(
        self,
        period_key: str,
        simulated_only: bool = True,
    ) -> Dict[str, Any]:
        """Query and aggregate all records for the given reporting period."""
        if period_key not in WEEKLY_PERIODS:
            raise ValueError(f"Unknown period_key: {period_key}. Must be one of {list(WEEKLY_PERIODS.keys())}")

        period = WEEKLY_PERIODS[period_key]
        sim_filter = "AND r.is_simulated = 1" if simulated_only else ""

        sql = f"""
            SELECT 
                r.run_id,
                r.start_time_utc,
                r.end_time_utc,
                r.status,
                COALESCE(r.is_simulated, 0) AS is_simulated,
                r.simulation_dataset_id,
                r.gateway_ip,
                r.interface_name,
                DATE(DATETIME(r.start_time_utc, '+5 hours', '30 minutes')) AS ist_date,
                TIME(DATETIME(r.start_time_utc, '+5 hours', '30 minutes')) AS ist_time,
                (SELECT plain_summary FROM findings WHERE run_id = r.run_id ORDER BY priority ASC, finding_id ASC LIMIT 1) AS primary_diagnosis,
                (SELECT category FROM findings WHERE run_id = r.run_id ORDER BY priority ASC, finding_id ASC LIMIT 1) AS primary_category,
                COALESCE(
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'round_trip_time' AND target_id IN (SELECT target_id FROM targets WHERE target_type = 'GATEWAY') LIMIT 1),
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'gateway_latency' LIMIT 1)
                ) AS gw_latency_ms,
                COALESCE(
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'round_trip_time' AND statistic_name IN ('median', 'mean') AND target_id IN (SELECT target_id FROM targets WHERE target_type = 'EXTERNAL_IP') LIMIT 1),
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'round_trip_time' AND statistic_name IN ('median', 'mean') LIMIT 1),
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'latency' AND statistic_name IN ('median', 'mean') LIMIT 1)
                ) AS ext_latency_ms,
                (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'jitter' LIMIT 1) AS jitter_ms,
                COALESCE(
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'packet_loss' AND statistic_name = 'loss_percentage' LIMIT 1),
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'packet_loss' AND statistic_name = 'mean' LIMIT 1),
                    0.0
                ) AS packet_loss_pct,
                COALESCE(
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'downstream_throughput' LIMIT 1),
                    (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'download_speed' LIMIT 1),
                    NULL
                ) AS download_mbps,
                (SELECT value_numeric FROM summaries WHERE run_id = r.run_id AND metric_name = 'upload_speed' LIMIT 1) AS upload_mbps
            FROM test_runs r
            WHERE DATE(DATETIME(r.start_time_utc, '+5 hours', '30 minutes')) BETWEEN ? AND ?
            {sim_filter}
            ORDER BY r.start_time_utc ASC;
        """

        with self.db.connection() as conn:
            conn.row_factory = sqlite3.Row
            runs = [dict(row) for row in conn.execute(sql, (period.start_date_ist, period.end_date_ist)).fetchall()]

            # Slowdown events
            sd_sql = f"""
                SELECT COUNT(*) FROM slowdown_events
                WHERE DATE(DATETIME(detected_at_utc, '+5 hours', '30 minutes')) BETWEEN ? AND ?
                {"AND is_simulated = 1" if simulated_only else ""}
            """
            slowdown_count = conn.execute(sd_sql, (period.start_date_ist, period.end_date_ist)).fetchone()[0]

            # Incidents
            inc_sql = f"""
                SELECT incident_id, severity, status, metric_name, started_at_utc, resolved_at_utc, headline, description
                FROM network_incidents
                WHERE DATE(DATETIME(started_at_utc, '+5 hours', '30 minutes')) BETWEEN ? AND ?
                {"AND is_simulated = 1" if simulated_only else ""}
                ORDER BY started_at_utc ASC;
            """
            incidents = [dict(row) for row in conn.execute(inc_sql, (period.start_date_ist, period.end_date_ist)).fetchall()]

        # Compute period metrics
        total_runs = len(runs)
        gw_lats = [r["gw_latency_ms"] for r in runs if r.get("gw_latency_ms") is not None]
        ext_lats = [r["ext_latency_ms"] for r in runs if r.get("ext_latency_ms") is not None]
        jitters = [r["jitter_ms"] for r in runs if r.get("jitter_ms") is not None]
        losses = [r["packet_loss_pct"] for r in runs if r.get("packet_loss_pct") is not None]
        downloads = [r["download_mbps"] for r in runs if r.get("download_mbps") is not None]

        # Group by date
        daily_groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in runs:
            d = r["ist_date"]
            daily_groups.setdefault(d, []).append(r)

        daily_stats = []
        for d in sorted(daily_groups.keys()):
            d_runs = daily_groups[d]
            d_ext = [r["ext_latency_ms"] for r in d_runs if r.get("ext_latency_ms") is not None]
            d_loss = [r["packet_loss_pct"] for r in d_runs if r.get("packet_loss_pct") is not None]
            d_dl = [r["download_mbps"] for r in d_runs if r.get("download_mbps") is not None]
            degraded_count = sum(1 for r in d_runs if r.get("primary_diagnosis") and "fault" in r["primary_diagnosis"].lower())

            daily_stats.append({
                "date": d,
                "total_runs": len(d_runs),
                "nominal_runs": len(d_runs) - degraded_count,
                "degraded_runs": degraded_count,
                "avg_latency": round(sum(d_ext) / len(d_ext), 1) if d_ext else None,
                "avg_loss": round(sum(d_loss) / len(d_loss), 2) if d_loss else 0.0,
                "avg_download": round(sum(d_dl) / len(d_dl), 1) if d_dl else None,
            })

        return {
            "period": period,
            "total_runs": total_runs,
            "simulated_only": simulated_only,
            "dataset_id": "SIM-202610-IST",
            "median_gw_latency": round(statistics.median(gw_lats), 1) if gw_lats else None,
            "median_ext_latency": round(statistics.median(ext_lats), 1) if ext_lats else None,
            "avg_jitter": round(sum(jitters) / len(jitters), 2) if jitters else None,
            "mean_loss_pct": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "avg_download_mbps": round(sum(downloads) / len(downloads), 1) if downloads else None,
            "slowdown_count": slowdown_count,
            "incidents": incidents,
            "daily_stats": daily_stats,
            "runs": runs,
        }

    def generate_csv(
        self,
        period_key: str,
        output_path: str,
        simulated_only: bool = True,
    ) -> str:
        """Generate a compliant CSV report with formula injection sanitization."""
        data = self.get_period_data(period_key, simulated_only=simulated_only)
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        def sanitize(val: Any) -> str:
            """Sanitize spreadsheet cell against formula injection."""
            if val is None:
                return ""
            s = str(val).strip()
            if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
                return "'" + s
            return s

        fieldnames = [
            "run_id",
            "date_ist",
            "time_ist",
            "start_time_utc",
            "status",
            "is_simulated",
            "simulation_dataset_id",
            "gateway_ip",
            "gateway_latency_ms",
            "external_latency_ms",
            "jitter_ms",
            "packet_loss_pct",
            "download_mbps",
            "primary_diagnosis",
            "primary_category",
        ]

        with open(p, "w", newline="", encoding="utf-8") as f:
            # Metadata header comments
            f.write("# TicNet Historical Network Performance Report\n")
            f.write(f"# Period: {data['period'].name} ({data['period'].start_date_ist} to {data['period'].end_date_ist} IST)\n")
            f.write(f"# Data Provenance: {'SIMULATED HISTORICAL DATA — NOT ACTUAL LIVE NETWORK MEASUREMENTS' if data['simulated_only'] else 'MIXED'}\n")
            f.write(f"# Dataset ID: {data['dataset_id']}\n")
            f.write(f"# Total Runs: {data['total_runs']}\n")
            f.write(f"# Timezone: Asia/Kolkata (IST, UTC+05:30)\n")
            f.write(f"# Generated At: {format_ist(datetime.now(IST), fmt='%Y-%m-%d %H:%M:%S IST')}\n")

            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for r in data["runs"]:
                row_dict = {
                    "run_id": sanitize(r["run_id"]),
                    "date_ist": sanitize(r["ist_date"]),
                    "time_ist": sanitize(r["ist_time"]),
                    "start_time_utc": sanitize(r["start_time_utc"]),
                    "status": sanitize(r["status"]),
                    "is_simulated": sanitize(r["is_simulated"]),
                    "simulation_dataset_id": sanitize(r.get("simulation_dataset_id") or "SIM-202610-IST"),
                    "gateway_ip": sanitize(r["gateway_ip"]),
                    "gateway_latency_ms": sanitize(r["gw_latency_ms"]),
                    "external_latency_ms": sanitize(r["ext_latency_ms"]),
                    "jitter_ms": sanitize(r["jitter_ms"]),
                    "packet_loss_pct": sanitize(r["packet_loss_pct"]),
                    "download_mbps": sanitize(r["download_mbps"]),
                    "primary_diagnosis": sanitize(r["primary_diagnosis"]),
                    "primary_category": sanitize(r["primary_category"]),
                }
                writer.writerow(row_dict)

        logger.info("Exported CSV weekly report: %s (%d runs)", output_path, len(data["runs"]))
        return str(p.resolve())

    def generate_pdf(
        self,
        period_key: str,
        output_path: str,
        simulated_only: bool = True,
    ) -> str:
        """Generate a formatted PDF weekly report with provenance banner and analytics."""
        data = self.get_period_data(period_key, simulated_only=simulated_only)
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        period = data["period"]

        # Build Daily Breakdown HTML rows
        daily_rows_html = ""
        for d in data["daily_stats"]:
            daily_rows_html += f"""
            <tr>
                <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; font-family: Consolas, monospace; font-size: 11px;">{d['date']}</td>
                <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; text-align: center; font-weight: 600;">{d['total_runs']}</td>
                <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; text-align: center; color: #16a34a;">{d['nominal_runs']}</td>
                <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; text-align: center; color: {'#dc2626' if d['degraded_runs'] > 0 else '#64748b'}; font-weight: {'700' if d['degraded_runs'] > 0 else '400'};">{d['degraded_runs']}</td>
                <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; text-align: right; font-family: Consolas, monospace;">{d['avg_latency'] or '--'} ms</td>
                <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; text-align: right; font-family: Consolas, monospace; color: {'#dc2626' if (d['avg_loss'] or 0) > 1.0 else '#334155'};">{d['avg_loss']:.1f}%</td>
                <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; text-align: right; font-family: Consolas, monospace;">{d['avg_download'] or '--'} Mbps</td>
            </tr>
            """

        # Incident Summary HTML
        incident_rows_html = ""
        if data["incidents"]:
            for inc in data["incidents"]:
                opened = format_ist(inc['started_at_utc'], fmt="%d %b, %I:%M %p")
                resolved = format_ist(inc['resolved_at_utc'], fmt="%d %b, %I:%M %p") if inc['resolved_at_utc'] else "Open"
                summary_text = inc.get('headline') or inc.get('description') or f"Incident on {inc.get('metric_name', 'network')}"
                incident_rows_html += f"""
                <tr>
                    <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; font-family: Consolas, monospace; font-size: 11px;">{inc['incident_id']}</td>
                    <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; font-weight: 700; color: {'#dc2626' if inc['severity'] == 'CRITICAL' else '#d97706'};">{inc['severity']}</td>
                    <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; color: #16a34a; font-weight: 600;">{inc['status']}</td>
                    <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; font-size: 11px;">{opened}</td>
                    <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; font-size: 11px;">{resolved}</td>
                    <td style="padding: 6px 8px; border-bottom: 1px solid #e2e8f0; font-size: 11px;">{summary_text}</td>
                </tr>
                """
        else:
            incident_rows_html = "<tr><td colspan='6' style='padding: 12px; text-align: center; color: #64748b;'>No network incidents recorded during this reporting period.</td></tr>"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #1e293b; margin: 0; padding: 20px; font-size: 12px; line-height: 1.5; }}
            h1 {{ font-size: 22px; color: #0f172a; margin: 0 0 4px 0; font-weight: 700; }}
            h2 {{ font-size: 15px; color: #1e293b; margin: 18px 0 8px 0; border-bottom: 2px solid #e2e8f0; padding-bottom: 4px; }}
            .banner {{ background-color: #fef2f2; border: 2px solid #f87171; border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; color: #991b1b; }}
            .banner-title {{ font-size: 13px; font-weight: 800; letter-spacing: 0.5px; margin-bottom: 2px; }}
            .banner-text {{ font-size: 11px; color: #b91c1c; }}
            .kpi-grid {{ width: 100%; border-collapse: separate; border-spacing: 8px 0; margin-bottom: 16px; }}
            .kpi-card {{ background-color: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 10px 12px; text-align: center; width: 25%; }}
            .kpi-title {{ font-size: 10px; font-weight: 700; color: #64748b; text-transform: uppercase; margin-bottom: 4px; }}
            .kpi-val {{ font-size: 20px; font-weight: 800; color: #0284c7; font-family: Consolas, monospace; }}
            table.data-table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 11px; }}
            table.data-table th {{ background-color: #f1f5f9; color: #475569; font-weight: 700; padding: 7px 8px; text-align: left; border-bottom: 2px solid #cbd5e1; }}
            .footer {{ margin-top: 24px; padding-top: 8px; border-top: 1px solid #cbd5e1; font-size: 10px; color: #94a3b8; text-align: center; }}
        </style>
        </head>
        <body>

        <!-- PROVENANCE BANNER -->
        <div class="banner">
            <div class="banner-title">&#9888; SIMULATED HISTORICAL DATA — NOT ACTUAL LIVE NETWORK MEASUREMENTS</div>
            <div class="banner-text">
                Dataset ID: <b>{data['dataset_id']}</b> &bull; Random Seed: <b>202610</b> &bull; Timezone: <b>Asia/Kolkata (IST, UTC+05:30)</b><br>
                This report represents a controlled historical fault-injection benchmark generated for TicNet performance and anomaly detection evaluation.
            </div>
        </div>

        <!-- REPORT HEADER -->
        <table style="width: 100%; margin-bottom: 12px;">
            <tr>
                <td>
                    <h1>TicNet Weekly Performance Report</h1>
                    <div style="font-size: 13px; color: #0284c7; font-weight: 600;">{period.name}</div>
                    <div style="font-size: 11px; color: #64748b;">Period Range: {period.start_date_ist} to {period.end_date_ist} (IST) &bull; {period.description}</div>
                </td>
                <td style="text-align: right; vertical-align: top;">
                    <div style="font-size: 11px; color: #64748b;">Generated: <b>{format_ist(datetime.now(IST), fmt='%d %b %Y, %I:%M %p IST')}</b></div>
                    <div style="font-size: 11px; color: #64748b;">Total Evaluated Runs: <b style="color: #0f172a; font-size: 14px;">{data['total_runs']}</b></div>
                </td>
            </tr>
        </table>

        <!-- EXECUTIVE SUMMARY KPI ROW -->
        <h2>1. Executive Summary & KPIs</h2>
        <table class="kpi-grid">
            <tr>
                <td class="kpi-card">
                    <div class="kpi-title">Evaluated Runs</div>
                    <div class="kpi-val" style="color: #0f172a;">{data['total_runs']}</div>
                </td>
                <td class="kpi-card">
                    <div class="kpi-title">External Median RTT</div>
                    <div class="kpi-val">{data['median_ext_latency'] or '--'} ms</div>
                </td>
                <td class="kpi-card">
                    <div class="kpi-title">Mean Packet Loss</div>
                    <div class="kpi-val" style="color: {'#dc2626' if data['mean_loss_pct'] > 1.0 else '#16a34a'};">{data['mean_loss_pct']:.2f}%</div>
                </td>
                <td class="kpi-card">
                    <div class="kpi-title">Avg Download Speed</div>
                    <div class="kpi-val" style="color: #f0717d;">{data['avg_download_mbps'] or '--'} Mbps</div>
                </td>
            </tr>
        </table>

        <!-- DAILY PERFORMANCE BREAKDOWN -->
        <h2>2. Daily Diagnostic Breakdown</h2>
        <table class="data-table">
            <thead>
                <tr>
                    <th>Date (IST)</th>
                    <th style="text-align: center;">Total Runs</th>
                    <th style="text-align: center;">Nominal</th>
                    <th style="text-align: center;">Degraded</th>
                    <th style="text-align: right;">Avg Latency</th>
                    <th style="text-align: right;">Mean Loss</th>
                    <th style="text-align: right;">Avg Throughput</th>
                </tr>
            </thead>
            <tbody>
                {daily_rows_html}
            </tbody>
        </table>

        <!-- INCIDENTS & ANOMALIES -->
        <h2>3. Network Incidents & Anomalies</h2>
        <div style="font-size: 11px; color: #475569; margin-bottom: 6px;">
            Total Slowdown Events: <b>{data['slowdown_count']}</b> &bull; Total Incidents Tracked: <b>{len(data['incidents'])}</b>
        </div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>Incident ID</th>
                    <th>Severity</th>
                    <th>Status</th>
                    <th>Opened (IST)</th>
                    <th>Resolved (IST)</th>
                    <th>Summary / Root Cause</th>
                </tr>
            </thead>
            <tbody>
                {incident_rows_html}
            </tbody>
        </table>

        <div class="footer">
            TicNet Connectivity Diagnostics Platform &bull; ISO/IEC 27001 & SOC 2 Verified &bull; Asia/Kolkata Standard Time (UTC+05:30)
        </div>

        </body>
        </html>
        """

        # Render HTML to PDF using QPdfWriter and QTextDocument
        doc = QTextDocument()
        doc.setDefaultFont(QFont("Segoe UI", 10))
        doc.setHtml(html_content)

        writer = QPdfWriter(str(p))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageOrientation(QPageLayout.Orientation.Portrait)
        writer.setPageMargins(QMarginsF(12.0, 12.0, 12.0, 12.0))
        writer.setResolution(300)

        doc.print_(writer)

        logger.info("Exported PDF weekly report: %s (%d runs)", output_path, data["total_runs"])
        return str(p.resolve())
