"""
Analytics Service — pipeline conversion metrics for the v2 intent signal system.

Provides read-only aggregate queries across signals, prospects, drafts, and
enrollments to surface conversion rates and pipeline health.
"""
import logging
from typing import Optional

from v2.db import db_connection, rows_to_dicts, row_to_dict

logger = logging.getLogger(__name__)


def get_pipeline_summary() -> dict:
    """Overall pipeline funnel: signals → prospects → drafts → enrollments.

    Returns counts at each stage plus conversion rates.
    """
    with db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as cnt FROM intent_signals")
        total_signals = _val(cursor.fetchone())

        cursor.execute("SELECT COUNT(*) as cnt FROM prospects WHERE do_not_contact = 0")
        total_prospects = _val(cursor.fetchone())

        cursor.execute("SELECT COUNT(DISTINCT prospect_id) as cnt FROM drafts")
        prospects_with_drafts = _val(cursor.fetchone())

        cursor.execute("SELECT COUNT(*) as cnt FROM prospects WHERE enrollment_status = 'enrolled'")
        enrolled = _val(cursor.fetchone())

        cursor.execute("SELECT COUNT(*) as cnt FROM prospects WHERE enrollment_status = 'sequence_complete'")
        completed = _val(cursor.fetchone())

        return {
            'total_signals': total_signals,
            'total_prospects': total_prospects,
            'prospects_with_drafts': prospects_with_drafts,
            'enrolled': enrolled,
            'sequence_complete': completed,
            'conversion_rates': {
                'signal_to_prospect': _rate(total_prospects, total_signals),
                'prospect_to_draft': _rate(prospects_with_drafts, total_prospects),
                'draft_to_enrolled': _rate(enrolled, prospects_with_drafts),
                'enrolled_to_complete': _rate(completed, enrolled),
                'signal_to_enrolled': _rate(enrolled, total_signals),
            },
        }


def get_signal_type_breakdown() -> list:
    """Signal counts grouped by signal_type, sorted by count descending."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT signal_type, COUNT(*) as cnt
            FROM intent_signals
            WHERE signal_type IS NOT NULL
            GROUP BY signal_type
            ORDER BY cnt DESC
        ''')
        return rows_to_dicts(cursor.fetchall())


def get_account_status_breakdown() -> list:
    """Account counts grouped by account_status."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT account_status, COUNT(*) as cnt
            FROM monitored_accounts
            WHERE archived_at IS NULL AND account_status IS NOT NULL
            GROUP BY account_status
            ORDER BY CASE account_status
                WHEN 'new' THEN 1
                WHEN 'sequenced' THEN 2
                WHEN 'revisit' THEN 3
                WHEN 'noise' THEN 4
                ELSE 5
            END
        ''')
        return rows_to_dicts(cursor.fetchall())


def get_campaign_performance() -> list:
    """Per-campaign metrics: signals, prospects, enrollments, conversion rate."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT
                c.id as campaign_id,
                c.name,
                COUNT(DISTINCT s.id) as signal_count,
                COUNT(DISTINCT p.id) as prospect_count,
                COUNT(DISTINCT CASE WHEN p.enrollment_status = 'enrolled' THEN p.id END) as enrolled_count,
                COUNT(DISTINCT CASE WHEN p.enrollment_status = 'sequence_complete' THEN p.id END) as complete_count
            FROM campaigns c
            LEFT JOIN intent_signals s ON s.recommended_campaign_id = c.id
            LEFT JOIN prospects p ON p.signal_id = s.id AND p.do_not_contact = 0
            GROUP BY c.id, c.name
            HAVING signal_count > 0
            ORDER BY signal_count DESC
        ''')
        rows = rows_to_dicts(cursor.fetchall())

        for row in rows:
            row['conversion_rate'] = _rate(
                row.get('enrolled_count', 0),
                row.get('prospect_count', 0),
            )
        return rows


def get_draft_quality_metrics() -> dict:
    """Draft generation and approval metrics.

    Returns: total drafts, approval rates, avg regenerations per prospect.
    """
    with db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as cnt FROM drafts")
        total_drafts = _val(cursor.fetchone())

        cursor.execute("SELECT COUNT(*) as cnt FROM drafts WHERE status = 'approved'")
        approved = _val(cursor.fetchone())

        cursor.execute("SELECT COUNT(*) as cnt FROM drafts WHERE status = 'enrolled'")
        enrolled_drafts = _val(cursor.fetchone())

        cursor.execute("SELECT COUNT(*) as cnt FROM feedback_log")
        total_regenerations = _val(cursor.fetchone())

        cursor.execute("SELECT COUNT(DISTINCT prospect_id) as cnt FROM feedback_log")
        prospects_with_feedback = _val(cursor.fetchone())

        return {
            'total_drafts': total_drafts,
            'approved': approved,
            'enrolled': enrolled_drafts,
            'approval_rate': _rate(approved + enrolled_drafts, total_drafts),
            'total_regenerations': total_regenerations,
            'avg_regenerations_per_prospect': round(
                total_regenerations / prospects_with_feedback, 1
            ) if prospects_with_feedback > 0 else 0,
        }


def get_enrollment_outcomes() -> dict:
    """Enrollment status distribution across all prospects."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT enrollment_status, COUNT(*) as cnt
            FROM prospects
            WHERE do_not_contact = 0
            GROUP BY enrollment_status
            ORDER BY cnt DESC
        ''')
        breakdown = rows_to_dicts(cursor.fetchall())

        total = sum(r.get('cnt', 0) for r in breakdown)
        return {
            'total_prospects': total,
            'breakdown': breakdown,
        }


def get_signal_source_breakdown() -> list:
    """Signal counts grouped by signal_source (csv, manual_entry, scan, webhook, cowork)."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT signal_source, COUNT(*) as cnt
            FROM intent_signals
            GROUP BY signal_source
            ORDER BY cnt DESC
        ''')
        return rows_to_dicts(cursor.fetchall())


def get_recent_activity_summary(days: int = 7) -> dict:
    """Activity counts by event_type over the last N days."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT event_type, COUNT(*) as cnt
            FROM activity_log
            WHERE created_at >= datetime('now', ?)
            GROUP BY event_type
            ORDER BY cnt DESC
        ''', (f'-{days} days',))
        return {
            'days': days,
            'events': rows_to_dicts(cursor.fetchall()),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _val(row):
    """Extract a single count value from a row."""
    if row is None:
        return 0
    if isinstance(row, dict):
        return row.get('cnt', 0)
    return row[0] if row else 0


def _rate(numerator, denominator):
    """Calculate a percentage rate, rounded to 1 decimal."""
    if not denominator or denominator == 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)
