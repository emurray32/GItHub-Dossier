"""
V2 Analytics Routes — read-only pipeline metrics and conversion data.

Blueprint: analytics_bp, prefix /v2/api/analytics
"""
import logging

from flask import Blueprint, request, jsonify

from validators import validate_positive_int

logger = logging.getLogger(__name__)

analytics_bp = Blueprint('v2_analytics', __name__, url_prefix='/v2/api/analytics')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(message, status_code=400):
    return jsonify({'status': 'error', 'message': message}), status_code


def _success(**kwargs):
    return jsonify({'status': 'success', **kwargs})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@analytics_bp.route('/pipeline', methods=['GET'])
def pipeline_summary():
    """Full pipeline funnel: signals → prospects → drafts → enrollments."""
    try:
        from v2.services.analytics_service import get_pipeline_summary
        return _success(pipeline=get_pipeline_summary())
    except Exception:
        logger.exception("[ANALYTICS] Error fetching pipeline summary")
        return _error('Internal server error', 500)


@analytics_bp.route('/signals', methods=['GET'])
def signal_breakdown():
    """Signal counts by type and source."""
    try:
        from v2.services.analytics_service import (
            get_signal_type_breakdown, get_signal_source_breakdown,
        )
        return _success(
            by_type=get_signal_type_breakdown(),
            by_source=get_signal_source_breakdown(),
        )
    except Exception:
        logger.exception("[ANALYTICS] Error fetching signal breakdown")
        return _error('Internal server error', 500)


@analytics_bp.route('/accounts', methods=['GET'])
def account_breakdown():
    """Account counts by workflow status."""
    try:
        from v2.services.analytics_service import get_account_status_breakdown
        return _success(accounts=get_account_status_breakdown())
    except Exception:
        logger.exception("[ANALYTICS] Error fetching account breakdown")
        return _error('Internal server error', 500)


@analytics_bp.route('/campaigns', methods=['GET'])
def campaign_performance():
    """Per-campaign signal, prospect, and enrollment metrics."""
    try:
        from v2.services.analytics_service import get_campaign_performance
        return _success(campaigns=get_campaign_performance())
    except Exception:
        logger.exception("[ANALYTICS] Error fetching campaign performance")
        return _error('Internal server error', 500)


@analytics_bp.route('/drafts', methods=['GET'])
def draft_quality():
    """Draft generation, approval, and regeneration metrics."""
    try:
        from v2.services.analytics_service import get_draft_quality_metrics
        return _success(drafts=get_draft_quality_metrics())
    except Exception:
        logger.exception("[ANALYTICS] Error fetching draft metrics")
        return _error('Internal server error', 500)


@analytics_bp.route('/enrollments', methods=['GET'])
def enrollment_outcomes():
    """Enrollment status distribution."""
    try:
        from v2.services.analytics_service import get_enrollment_outcomes
        return _success(enrollments=get_enrollment_outcomes())
    except Exception:
        logger.exception("[ANALYTICS] Error fetching enrollment outcomes")
        return _error('Internal server error', 500)


@analytics_bp.route('/activity', methods=['GET'])
def recent_activity():
    """Activity event counts over the last N days (default 7)."""
    try:
        days = request.args.get('days', '7')
        valid, days = validate_positive_int(days, 'days', max_val=90)
        if not valid:
            return _error(days)

        from v2.services.analytics_service import get_recent_activity_summary
        return _success(activity=get_recent_activity_summary(days))
    except Exception:
        logger.exception("[ANALYTICS] Error fetching activity summary")
        return _error('Internal server error', 500)


@analytics_bp.route('/overview', methods=['GET'])
def overview():
    """Combined overview — all metrics in one call for dashboards."""
    try:
        from v2.services.analytics_service import (
            get_pipeline_summary,
            get_account_status_breakdown,
            get_campaign_performance,
            get_draft_quality_metrics,
            get_enrollment_outcomes,
            get_recent_activity_summary,
        )
        return _success(
            pipeline=get_pipeline_summary(),
            accounts=get_account_status_breakdown(),
            campaigns=get_campaign_performance(),
            drafts=get_draft_quality_metrics(),
            enrollments=get_enrollment_outcomes(),
            activity=get_recent_activity_summary(7),
        )
    except Exception:
        logger.exception("[ANALYTICS] Error fetching overview")
        return _error('Internal server error', 500)
