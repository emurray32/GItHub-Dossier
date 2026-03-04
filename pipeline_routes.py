"""
Flask Blueprint for Pipeline Orchestrator API routes.

Register this blueprint in app.py:
    from pipeline_routes import pipeline_bp
    app.register_blueprint(pipeline_bp)
"""
import logging
import threading
from flask import Blueprint, jsonify, request
from validators import validate_positive_int, validate_company_name, validate_apollo_id

pipeline_bp = Blueprint('pipeline', __name__)


@pipeline_bp.route('/api/pipeline/status', methods=['GET'])
def api_pipeline_status():
    """Return current state of all pipeline jobs, last run times, success/failure counts."""
    try:
        from pipeline import PipelineOrchestrator
        orchestrator = PipelineOrchestrator.instance()
        return jsonify({'status': 'success', **orchestrator.get_status()})
    except ImportError:
        return jsonify({'status': 'error', 'message': 'Pipeline module not available'}), 503
    except Exception as e:
        logging.error("[PIPELINE] %s: %s", request.path, e)
        return jsonify({'status': 'error', 'message': 'Internal error. Check server logs.'}), 500


@pipeline_bp.route('/api/pipeline/health', methods=['GET'])
def api_pipeline_health():
    """Return DB connectivity, API key validity, scheduler status."""
    try:
        from pipeline import PipelineOrchestrator
        orchestrator = PipelineOrchestrator.instance()
        health = orchestrator.get_health()
        status_code = 200 if health.get('status') == 'healthy' else 503
        return jsonify(health), status_code
    except ImportError:
        return jsonify({'status': 'unhealthy', 'message': 'Pipeline module not available'}), 503
    except Exception as e:
        logging.error("[PIPELINE] Health check failed: %s", e)
        return jsonify({'status': 'unhealthy', 'message': 'Health check failed. Check server logs.'}), 503


@pipeline_bp.route('/api/pipeline/trigger', methods=['POST'])
def api_pipeline_trigger():
    """Manually trigger a full pipeline run."""
    try:
        from pipeline import PipelineOrchestrator
        orchestrator = PipelineOrchestrator.instance()
        if orchestrator.is_paused():
            return jsonify({'status': 'error', 'message': 'Pipeline is paused. Resume it first.'}), 409

        # Run in background thread to avoid blocking the request
        def _run():
            orchestrator.run_full_pipeline(trigger_type='manual')

        thread = threading.Thread(target=_run, daemon=True, name="PipelineManualTrigger")
        thread.start()
        return jsonify({'status': 'success', 'message': 'Pipeline run triggered'})
    except ImportError:
        return jsonify({'status': 'error', 'message': 'Pipeline module not available'}), 503
    except Exception as e:
        logging.error("[PIPELINE] %s: %s", request.path, e)
        return jsonify({'status': 'error', 'message': 'Internal error. Check server logs.'}), 500


@pipeline_bp.route('/api/pipeline/pause', methods=['POST'])
def api_pipeline_pause():
    """Pause or resume the pipeline."""
    try:
        from pipeline import PipelineOrchestrator
        orchestrator = PipelineOrchestrator.instance()
        data = request.get_json() or {}
        action = data.get('action', 'pause')

        if action == 'resume':
            orchestrator.resume()
            return jsonify({'status': 'success', 'message': 'Pipeline resumed', 'paused': False})
        else:
            orchestrator.pause()
            return jsonify({'status': 'success', 'message': 'Pipeline paused', 'paused': True})
    except ImportError:
        return jsonify({'status': 'error', 'message': 'Pipeline module not available'}), 503
    except Exception as e:
        logging.error("[PIPELINE] %s: %s", request.path, e)
        return jsonify({'status': 'error', 'message': 'Internal error. Check server logs.'}), 500


@pipeline_bp.route('/api/pipeline/runs', methods=['GET'])
def api_pipeline_runs():
    """Return recent pipeline runs with optional detail for a specific run."""
    try:
        from pipeline import get_recent_runs, get_run_steps
        run_id = request.args.get('run_id')
        if run_id:
            is_valid, result = validate_positive_int(run_id, name='run_id')
            if not is_valid:
                return jsonify({'status': 'error', 'message': result}), 400
            steps = get_run_steps(result)
            return jsonify({'status': 'success', 'steps': steps})
        limit_str = request.args.get('limit', 20)
        is_valid, result = validate_positive_int(limit_str, name='limit', max_val=500)
        if not is_valid:
            return jsonify({'status': 'error', 'message': result}), 400
        runs = get_recent_runs(limit=result)
        return jsonify({'status': 'success', 'runs': runs})
    except ImportError:
        return jsonify({'status': 'error', 'message': 'Pipeline module not available'}), 503
    except Exception as e:
        logging.error("[PIPELINE] %s: %s", request.path, e)
        return jsonify({'status': 'error', 'message': 'Internal error. Check server logs.'}), 500


@pipeline_bp.route('/api/pipeline/circuit-breakers', methods=['GET'])
def api_circuit_breakers():
    """Return status of all circuit breakers."""
    try:
        from circuit_breaker import CircuitBreaker
        breakers = {
            name: cb.status()
            for name, cb in CircuitBreaker.get_all().items()
        }
        return jsonify({'status': 'success', 'circuit_breakers': breakers})
    except ImportError:
        return jsonify({'status': 'error', 'message': 'Circuit breaker module not available'}), 503
    except Exception as e:
        logging.error("[PIPELINE] %s: %s", request.path, e)
        return jsonify({'status': 'error', 'message': 'Internal error. Check server logs.'}), 500


@pipeline_bp.route('/api/pipeline/circuit-breakers/<service>/reset', methods=['POST'])
def api_circuit_breaker_reset(service):
    """Manually reset a circuit breaker for a given service."""
    try:
        from circuit_breaker import CircuitBreaker
        breakers = CircuitBreaker.get_all()
        if service not in breakers:
            return jsonify({'status': 'error', 'message': f'No circuit breaker for service: {service}'}), 404
        breakers[service].force_reset()
        return jsonify({
            'status': 'success',
            'message': f'Circuit breaker for {service} reset',
            'breaker': breakers[service].status(),
        })
    except ImportError:
        return jsonify({'status': 'error', 'message': 'Circuit breaker module not available'}), 503
    except Exception as e:
        logging.error("[PIPELINE] %s: %s", request.path, e)
        return jsonify({'status': 'error', 'message': 'Internal error. Check server logs.'}), 500


@pipeline_bp.route('/api/pipeline/config', methods=['GET', 'POST'])
def api_pipeline_config():
    """Get or update pipeline configuration."""
    if request.method == 'GET':
        try:
            from pipeline import _get_config, _DEFAULT_CONFIG
            config = {key: _get_config(key) for key in _DEFAULT_CONFIG}
            return jsonify({'status': 'success', 'config': config})
        except ImportError:
            return jsonify({'status': 'error', 'message': 'Pipeline module not available'}), 503

    # POST — update config
    try:
        from pipeline import _get_config, _set_config, _DEFAULT_CONFIG
        data = request.get_json() or {}

        # Validate against actual config keys
        unknown_keys = set(data.keys()) - set(_DEFAULT_CONFIG.keys())
        if unknown_keys:
            return jsonify({
                'status': 'error',
                'message': f'Unknown config keys: {", ".join(sorted(unknown_keys))}',
            }), 400

        updated = {}
        for key, value in data.items():
            if key in _DEFAULT_CONFIG:
                _set_config(key, value)
                updated[key] = value
        config = {key: _get_config(key) for key in _DEFAULT_CONFIG}
        return jsonify({'status': 'success', 'updated': updated, 'config': config})
    except ImportError:
        return jsonify({'status': 'error', 'message': 'Pipeline module not available'}), 503
    except Exception as e:
        logging.error("[PIPELINE] %s: %s", request.path, e)
        return jsonify({'status': 'error', 'message': 'Internal error. Check server logs.'}), 500


# ---------------------------------------------------------------------------
# Apollo Pipeline Routes — Contact Discovery & Bulk Enrollment
# ---------------------------------------------------------------------------

@pipeline_bp.route('/api/pipeline/discover-contacts', methods=['POST'])
def api_pipeline_discover_contacts():
    """Trigger contact discovery for specified account IDs.

    Request JSON:
        account_ids: list of monitored_accounts.id
        campaign_id: optional campaign ID for personas and batch tracking
        async: optional bool (default false) — run in background thread
    """
    try:
        from apollo_pipeline import auto_discover_contacts
        from database import (get_campaign_personas, create_enrollment_batch,
                              update_enrollment_batch)
        from datetime import datetime
    except ImportError:
        return jsonify({'status': 'error', 'message': 'Apollo pipeline module not available'}), 503

    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    account_ids = data.get('account_ids', [])
    if not account_ids or not isinstance(account_ids, list):
        return jsonify({'status': 'error', 'message': 'account_ids must be a non-empty list'}), 400

    # Validate each account ID
    validated_ids = []
    for aid in account_ids:
        is_valid, result = validate_positive_int(aid, name='account_id')
        if not is_valid:
            return jsonify({'status': 'error', 'message': result}), 400
        validated_ids.append(result)
    account_ids = validated_ids

    campaign_id = data.get('campaign_id')
    run_async = data.get('async', False)

    personas = None
    if campaign_id:
        personas = get_campaign_personas(campaign_id)

    batch_id = None
    if campaign_id:
        batch_id = create_enrollment_batch(campaign_id, account_ids)
        update_enrollment_batch(batch_id, status='in_progress', current_phase='discovering',
                                started_at=datetime.utcnow().isoformat())

    if run_async:
        def _run_discovery():
            try:
                total_new = 0
                for aid in account_ids:
                    result = auto_discover_contacts(aid, batch_id=batch_id, personas=personas)
                    total_new += result.get('new', 0)
                if batch_id:
                    update_enrollment_batch(batch_id, discovered=total_new,
                                            current_phase='discovery_complete')
            except Exception as e:
                logging.error("[PIPELINE] Background discovery failed: %s", e)
                if batch_id:
                    update_enrollment_batch(batch_id, status='failed',
                                            error_message=str(e)[:500])

        t = threading.Thread(target=_run_discovery, daemon=True)
        t.start()
        return jsonify({
            'status': 'started',
            'batch_id': batch_id,
            'message': f'Discovery started for {len(account_ids)} accounts',
        })

    results = {}
    total_new = 0
    for aid in account_ids:
        result = auto_discover_contacts(aid, batch_id=batch_id, personas=personas)
        results[str(aid)] = {
            'new': result.get('new', 0),
            'skipped_dedup': result.get('skipped_dedup', 0),
            'total': result.get('total', 0),
            'error': result.get('error'),
        }
        total_new += result.get('new', 0)

    if batch_id:
        update_enrollment_batch(batch_id, discovered=total_new,
                                current_phase='discovery_complete')

    return jsonify({
        'status': 'success',
        'batch_id': batch_id,
        'total_new_contacts': total_new,
        'results': results,
    })


@pipeline_bp.route('/api/pipeline/bulk-enroll', methods=['POST'])
def api_pipeline_bulk_enroll():
    """Trigger bulk enrollment for a batch.

    Request JSON:
        batch_id: enrollment_batches.id (required)
        contact_ids: optional list of enrollment_contacts.id to process
        limit: optional max contacts per call (default 25)
        async: optional bool (default false) — run in background thread
    """
    try:
        from apollo_pipeline import bulk_enroll_contacts
        from database import get_enrollment_batch
    except ImportError:
        return jsonify({'status': 'error', 'message': 'Apollo pipeline module not available'}), 503

    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    batch_id = data.get('batch_id')
    if not batch_id:
        return jsonify({'status': 'error', 'message': 'batch_id is required'}), 400

    batch = get_enrollment_batch(batch_id)
    if not batch:
        return jsonify({'status': 'error', 'message': f'Batch {batch_id} not found'}), 404

    contact_ids = data.get('contact_ids')
    limit = data.get('limit', 25)
    run_async = data.get('async', False)

    if run_async:
        def _run_enrollment():
            try:
                bulk_enroll_contacts(batch_id, contact_ids=contact_ids, limit=limit)
            except Exception as e:
                logging.error("[PIPELINE] Background enrollment failed: %s", e)
                update_enrollment_batch(batch_id, status='failed',
                                        error_message=str(e)[:500])

        t = threading.Thread(target=_run_enrollment, daemon=True)
        t.start()
        return jsonify({
            'status': 'started',
            'batch_id': batch_id,
            'message': 'Bulk enrollment started',
        })

    result = bulk_enroll_contacts(batch_id, contact_ids=contact_ids, limit=limit)
    return jsonify({
        'status': 'success',
        'batch_id': batch_id,
        **result,
    })


@pipeline_bp.route('/api/pipeline/rate-limit-status')
def api_pipeline_rate_limit_status():
    """Check current Apollo rate limiter status."""
    try:
        from apollo_pipeline import rate_limiter
        return jsonify({
            'status': 'success',
            'available_tokens': rate_limiter.available_tokens,
            'max_tokens': 50,
            'refill_period_seconds': 60,
        })
    except ImportError:
        return jsonify({'status': 'error', 'message': 'Apollo pipeline module not available'}), 503
