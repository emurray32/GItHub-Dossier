"""
V2 MCP Tools — intent-signal-first workflow tools for Claude Code integration.

This module defines a register_v2_tools(mcp) function that takes the FastMCP
instance and registers all v2 tools. It is loaded lazily by mcp_server.py.

Each tool:
- Has a clear docstring (becomes the tool description in Claude)
- Imports services lazily inside the function body to avoid circular imports
- Returns json.dumps(result) as a string
- Handles errors with try/except, returning JSON error messages
"""
import json
import logging

logger = logging.getLogger(__name__)


def _safe_json(obj):
    """Serialize an object to JSON, handling datetimes."""
    def _default(o):
        if hasattr(o, 'isoformat'):
            return o.isoformat()
        return str(o)
    return json.dumps(obj, default=_default)


def register_v2_tools(mcp):
    """Register v2 intent-signal-first MCP tools."""

    # ------------------------------------------------------------------
    # Signal Queue
    # ------------------------------------------------------------------

    @mcp.tool()
    def list_signal_queue(status: str = "new", owner: str = None, limit: int = 20) -> str:
        """List intent signals in the queue, filtered by workflow status and/or owner.

        Returns signals sorted newest-first with account info. Use this to see
        what signals need attention.

        Args:
            status: Filter by workflow status ('new', 'sequenced', 'revisit', 'noise'). Default 'new'.
            owner: Filter by account owner name. Optional.
            limit: Max signals to return. Default 20.
        """
        try:
            from v2.services.signal_service import list_signals
            result = list_signals(status=status, owner=owner, limit=limit)
            return _safe_json(result)
        except Exception as e:
            logger.exception("[MCP] list_signal_queue error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def get_signal_workspace(signal_id: int) -> str:
        """Get the full workspace context for a signal.

        Returns signal details, account info, recommended campaign, personas,
        existing prospects, drafts, and writing preferences. This is the
        starting point for working on a signal.

        Args:
            signal_id: The intent signal ID to load.
        """
        try:
            from v2.services.signal_service import get_signal_workspace as _get_ws
            result = _get_ws(signal_id)
            if not result:
                return _safe_json({"error": f"Signal {signal_id} not found"})
            return _safe_json(result)
        except Exception as e:
            logger.exception("[MCP] get_signal_workspace error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def create_signal(account_name: str, signal_description: str, signal_type: str = None, evidence: str = None, website: str = None, github_org: str = None) -> str:
        """Create a new intent signal for an account.

        Finds or creates the account by name, then creates the signal.
        Use this when you discover a new localization intent signal.

        Args:
            account_name: Company name (will find existing or create new account).
            signal_description: What was observed (e.g. 'Added react-i18next to package.json').
            signal_type: Optional signal category (e.g. 'dependency_injection', 'rfc_discussion').
            evidence: Optional raw evidence text or URL.
            website: Optional company website URL (helps match/create the right account).
            github_org: Optional GitHub org login (stored on the account for scanning).
        """
        try:
            from v2.services.account_service import find_or_create_account
            from v2.services.signal_service import create_signal as _create

            account_id = find_or_create_account(account_name, website=website)

            # Auto-recommend campaign based on signal type
            rec_campaign_id = None
            rec_campaign_reasoning = None
            try:
                from v2.services.campaign_service import recommend_campaign as _rec
                rec = _rec(signal_type=signal_type, outreach_angle=signal_description)
                rec_campaign_id = rec.get('campaign_id')
                rec_campaign_reasoning = rec.get('reasoning')
            except Exception:
                pass

            signal_id = _create(
                account_id=account_id,
                signal_description=signal_description,
                signal_type=signal_type,
                evidence_type='cowork_push',
                evidence_value=evidence,
                signal_source='cowork',
                recommended_campaign_id=rec_campaign_id,
                recommended_campaign_reasoning=rec_campaign_reasoning,
                created_by='mcp',
            )

            # Log activity
            try:
                from v2.services.activity_service import log_activity
                log_activity(
                    event_type='signal_created',
                    entity_type='signal',
                    entity_id=signal_id,
                    details={
                        'account_name': account_name,
                        'account_id': account_id,
                        'signal_type': signal_type,
                    },
                    created_by='mcp',
                )
            except Exception:
                pass

            return _safe_json({
                "signal_id": signal_id,
                "account_id": account_id,
                "account_name": account_name,
                "recommended_campaign_id": rec_campaign_id,
                "recommended_campaign_reasoning": rec_campaign_reasoning,
                "message": f"Signal created (id={signal_id}) for {account_name}",
            })
        except Exception as e:
            logger.exception("[MCP] create_signal error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Campaign Recommendation
    # ------------------------------------------------------------------

    @mcp.tool()
    def recommend_campaign(signal_id: int) -> str:
        """Get a campaign recommendation for a signal.

        Analyzes the signal type and account context to recommend the best
        campaign to use for outreach.

        Args:
            signal_id: The signal to recommend a campaign for.
        """
        try:
            from v2.services.signal_service import get_signal

            signal = get_signal(signal_id)
            if not signal:
                return _safe_json({"error": f"Signal {signal_id} not found"})

            try:
                from v2.services.campaign_service import recommend_campaign as _recommend
                result = _recommend(
                    signal_type=signal.get('signal_type'),
                    outreach_angle=signal.get('signal_description'),
                )
            except ImportError:
                result = {
                    "campaign_id": None,
                    "campaign_name": "Default",
                    "reasoning": "Campaign service not yet available",
                }

            return _safe_json(result)
        except Exception as e:
            logger.exception("[MCP] recommend_campaign error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Prospect Discovery
    # ------------------------------------------------------------------

    @mcp.tool()
    def find_prospects(signal_id: int, campaign_id: int = 0, titles: str = "", seniorities: str = "") -> str:
        """Search Apollo for prospects matching the signal's account.

        Uses the campaign's persona hierarchy to find the right contacts.
        If the campaign has personas defined, searches Apollo tier by tier
        (priority 0 first, then 1, then 2) and tags each result with their
        matched persona. Falls back to manual titles/seniorities if provided,
        or generic defaults if no personas exist.

        Args:
            signal_id: The signal whose account to search.
            campaign_id: Campaign ID to look up persona tiers (recommended).
            titles: Manual override — comma-separated job titles. Ignored if campaign has personas.
            seniorities: Manual override — comma-separated seniority levels. Ignored if campaign has personas.
        """
        try:
            from v2.services.signal_service import get_signal
            from v2.services.account_service import get_account_domain
            from v2.services.campaign_service import get_personas_for_campaign

            signal = get_signal(signal_id)
            if not signal:
                return _safe_json({"error": f"Signal {signal_id} not found"})

            account_id = signal['account_id']
            domain = get_account_domain(account_id)
            if not domain:
                return _safe_json({"error": "Account has no website/domain configured"})

            from apollo_client import apollo_api_call

            # Build search tiers from campaign personas or fallback
            search_tiers = []

            if campaign_id:
                personas = get_personas_for_campaign(campaign_id)
                for p in sorted(personas, key=lambda x: x.get('priority', 0)):
                    tier_titles = p.get('titles', [])
                    tier_seniorities = p.get('seniorities', [])
                    if tier_titles or tier_seniorities:
                        search_tiers.append({
                            'persona_name': p.get('persona_name', 'Unknown'),
                            'titles': tier_titles,
                            'seniorities': tier_seniorities,
                            'sequence_id': p.get('sequence_id', ''),
                            'priority': p.get('priority', 0),
                        })

            if not search_tiers:
                # Fallback: use manual overrides or generic defaults
                fallback_titles = (
                    [t.strip() for t in titles.split(',') if t.strip()]
                    if titles else
                    ['VP Engineering', 'Head of Engineering', 'Head of Product', 'Director of Localization']
                )
                fallback_seniorities = (
                    [s.strip() for s in seniorities.split(',') if s.strip()]
                    if seniorities else
                    ['vp', 'director', 'c_suite']
                )
                search_tiers.append({
                    'persona_name': 'Default',
                    'titles': fallback_titles,
                    'seniorities': fallback_seniorities,
                    'sequence_id': '',
                    'priority': 0,
                })

            # Search Apollo tier by tier, dedup across tiers
            all_results = []
            seen_emails = set()
            tier_summaries = []

            for tier in search_tiers:
                search_body = {
                    'q_organization_domains': domain,
                    'person_titles': tier['titles'],
                    'page': 1,
                    'per_page': 10,  # smaller per tier to stay within rate limits
                }
                if tier['seniorities']:
                    search_body['person_seniorities'] = tier['seniorities']

                try:
                    resp = apollo_api_call(
                        'post',
                        'https://api.apollo.io/v1/mixed_people/search',
                        json=search_body,
                    )

                    if resp.status_code != 200:
                        tier_summaries.append({
                            'persona': tier['persona_name'],
                            'priority': tier['priority'],
                            'found': 0,
                            'error': f"Apollo returned {resp.status_code}",
                        })
                        continue

                    people = resp.json().get('people', [])
                    tier_count = 0

                    for person in people:
                        email = (person.get('email') or '').lower()
                        if email and email in seen_emails:
                            continue
                        if email:
                            seen_emails.add(email)

                        tier_count += 1
                        all_results.append({
                            'full_name': person.get('name', ''),
                            'first_name': person.get('first_name', ''),
                            'last_name': person.get('last_name', ''),
                            'title': person.get('title', ''),
                            'email': person.get('email', ''),
                            'email_verified': person.get('email_status') == 'verified',
                            'linkedin_url': person.get('linkedin_url', ''),
                            'apollo_person_id': person.get('id', ''),
                            'matched_persona': tier['persona_name'],
                            'persona_priority': tier['priority'],
                            'sequence_id': tier['sequence_id'],
                        })

                    tier_summaries.append({
                        'persona': tier['persona_name'],
                        'priority': tier['priority'],
                        'found': tier_count,
                        'titles_searched': tier['titles'],
                    })

                except Exception as tier_err:
                    logger.warning("[MCP] find_prospects tier %s error: %s", tier['persona_name'], tier_err)
                    tier_summaries.append({
                        'persona': tier['persona_name'],
                        'priority': tier['priority'],
                        'found': 0,
                        'error': str(tier_err),
                    })

            return _safe_json({
                "people": all_results,
                "total": len(all_results),
                "domain": domain,
                "signal_id": signal_id,
                "account_id": account_id,
                "campaign_id": campaign_id or None,
                "search_tiers": tier_summaries,
            })
        except Exception as e:
            logger.exception("[MCP] find_prospects error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Prospect Persistence
    # ------------------------------------------------------------------

    @mcp.tool()
    def save_prospects(signal_id: int, account_id: int, prospects: str) -> str:
        """Save found prospects to the shared prospects table.

        After using find_prospects to search Apollo, call this tool to persist
        the selected prospects so they appear in the web UI and can receive
        draft sequences.

        Args:
            signal_id: The signal these prospects belong to.
            account_id: The account these prospects belong to.
            prospects: JSON array of prospect objects. Each must have at least
                       'email' and 'full_name'. Optional fields: first_name,
                       last_name, title, email_verified, linkedin_url,
                       apollo_person_id.
        """
        try:
            import json as _json
            prospect_list = _json.loads(prospects) if isinstance(prospects, str) else prospects
            if not isinstance(prospect_list, list) or not prospect_list:
                return _safe_json({"error": "prospects must be a non-empty JSON array"})

            from v2.services.prospect_service import (
                bulk_create_prospects, is_already_enrolled, is_do_not_contact,
            )

            # Filter: skip DNC, enrolled, unverified, personal, no-email
            try:
                from email_utils import _filter_personal_email
            except ImportError:
                _filter_personal_email = lambda e: e  # allow all through if filter unavailable

            records = []
            skipped = {'enrolled': 0, 'personal': 0, 'no_email': 0, 'unverified': 0, 'dnc': 0}
            for p in prospect_list:
                email = (p.get('email') or '').strip().lower()
                if not email:
                    skipped['no_email'] += 1
                    continue
                if not p.get('email_verified'):
                    skipped['unverified'] += 1
                    continue
                if not _filter_personal_email(email):
                    skipped['personal'] += 1
                    continue
                if is_do_not_contact(email):
                    skipped['dnc'] += 1
                    continue
                if is_already_enrolled(email):
                    skipped['enrolled'] += 1
                    continue
                records.append({
                    'account_id': account_id,
                    'signal_id': signal_id,
                    'full_name': p.get('full_name', ''),
                    'first_name': p.get('first_name', ''),
                    'last_name': p.get('last_name', ''),
                    'title': p.get('title', ''),
                    'email': email,
                    'email_verified': p.get('email_verified', False),
                    'linkedin_url': p.get('linkedin_url', ''),
                    'apollo_person_id': p.get('apollo_person_id', ''),
                })

            if not records:
                return _safe_json({
                    "error": f"No valid prospects to save (skipped: {skipped['enrolled']} enrolled, "
                             f"{skipped['personal']} personal, {skipped['no_email']} no email, "
                             f"{skipped['unverified']} unverified, {skipped['dnc']} do-not-contact)",
                })

            ids = bulk_create_prospects(records)

            try:
                from v2.services.activity_service import log_activity
                log_activity(
                    event_type='prospects_saved',
                    entity_type='signal',
                    entity_id=signal_id,
                    details={
                        'count': len(ids),
                        'account_id': account_id,
                        'skipped': skipped,
                    },
                    created_by='mcp',
                )
            except Exception:
                pass

            return _safe_json({
                "prospect_ids": ids,
                "count": len(ids),
                "skipped": skipped,
                "message": f"Saved {len(ids)} prospects for signal {signal_id}",
            })
        except Exception as e:
            logger.exception("[MCP] save_prospects error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Draft Generation & Management
    # ------------------------------------------------------------------

    @mcp.tool()
    def generate_draft_sequence(prospect_id: int, signal_id: int, campaign_id: int,
                               user_email: str = "") -> str:
        """Generate a multi-step email draft sequence for a prospect.

        Creates 3 drafts (initial outreach, follow-up, breakup) using the
        signal context, campaign guidelines, and BDR's personal writing
        preferences. Uses AI when available, falls back to templates.

        Args:
            prospect_id: The prospect to generate drafts for.
            signal_id: The intent signal providing context.
            campaign_id: The campaign with writing guidelines.
            user_email: BDR's email for personal writing preference lookup.
        """
        try:
            from v2.services.draft_service import generate_drafts
            drafts = generate_drafts(
                prospect_id, signal_id, campaign_id,
                user_email=user_email if user_email else None,
            )
            return _safe_json({
                "drafts": drafts,
                "count": len(drafts),
                "prospect_id": prospect_id,
            })
        except ValueError as e:
            return _safe_json({"error": str(e)})
        except Exception as e:
            logger.exception("[MCP] generate_draft_sequence error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def regenerate_draft_step(draft_id: int, critique: str) -> str:
        """Regenerate a single draft step incorporating your feedback.

        Takes the original draft and your critique, then rewrites it.
        The critique is logged for learning over time.

        Args:
            draft_id: The draft to regenerate.
            critique: What to change (e.g. 'Make it shorter', 'Reference their specific repo').
        """
        try:
            from v2.services.draft_service import regenerate_draft
            draft = regenerate_draft(draft_id, critique)
            if not draft:
                return _safe_json({"error": f"Draft {draft_id} not found"})
            return _safe_json({"draft": draft})
        except Exception as e:
            logger.exception("[MCP] regenerate_draft_step error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def save_edited_draft(draft_id: int, subject: str = None, body: str = None) -> str:
        """Save manual edits to a draft's subject and/or body.

        Use this when you want to directly edit the text rather than
        regenerating via AI.

        Args:
            draft_id: The draft to update.
            subject: New subject line (or None to keep existing).
            body: New email body (or None to keep existing).
        """
        try:
            from v2.services.draft_service import update_draft
            draft = update_draft(draft_id, subject=subject, body=body)
            if not draft:
                return _safe_json({"error": f"Draft {draft_id} not found"})
            return _safe_json({"draft": draft})
        except Exception as e:
            logger.exception("[MCP] save_edited_draft error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def approve_draft(draft_id: int) -> str:
        """Mark a draft as approved and ready for enrollment.

        Approved drafts can be sent when the prospect is enrolled
        into an Apollo sequence.

        Args:
            draft_id: The draft to approve.
        """
        try:
            from v2.services.draft_service import approve_draft as _approve
            draft = _approve(draft_id)
            if not draft:
                return _safe_json({"error": f"Draft {draft_id} not found"})
            return _safe_json({"draft": draft})
        except Exception as e:
            logger.exception("[MCP] approve_draft error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Enrollment
    # ------------------------------------------------------------------

    @mcp.tool()
    def enroll_prospect(prospect_id: int, sequence_id: str = None) -> str:
        """Enroll a prospect into an Apollo email sequence.

        Requires approved drafts. Calls the Apollo API to add the contact
        to the specified (or default) sequence. Updates prospect status
        and account status.

        Args:
            prospect_id: The prospect to enroll.
            sequence_id: Optional Apollo sequence ID override.
        """
        try:
            from v2.services.enrollment_service import enroll_prospect as _enroll
            result = _enroll(prospect_id, sequence_id=sequence_id)
            return _safe_json(result)
        except Exception as e:
            logger.exception("[MCP] enroll_prospect error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Account Management
    # ------------------------------------------------------------------

    @mcp.tool()
    def mark_account_noise(account_id: int) -> str:
        """Mark an account as noise (false positive / not worth pursuing).

        This is a manual action — use when you determine an account's
        signals are not real buying intent.

        Args:
            account_id: The account to mark as noise.
        """
        try:
            from v2.services.account_service import mark_account_noise as _mark

            ok = _mark(account_id)
            if not ok:
                return _safe_json({"error": f"Failed to mark account {account_id} as noise"})

            try:
                from v2.services.activity_service import log_activity
                log_activity(
                    event_type='account_marked_noise',
                    entity_type='account',
                    entity_id=account_id,
                    created_by='mcp',
                )
            except Exception:
                pass

            return _safe_json({
                "account_id": account_id,
                "status": "noise",
                "message": f"Account {account_id} marked as noise",
            })
        except Exception as e:
            logger.exception("[MCP] mark_account_noise error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def mark_account_revisit(account_id: int) -> str:
        """Mark an account for revisit (sequences done, no reply yet).

        Use this when all outreach sequences have completed without
        response and you want to flag the account for future follow-up.

        Args:
            account_id: The account to mark for revisit.
        """
        try:
            from v2.services.account_service import mark_account_revisit as _mark

            ok = _mark(account_id)
            if not ok:
                return _safe_json({"error": f"Failed to mark account {account_id} for revisit"})

            return _safe_json({
                "account_id": account_id,
                "status": "revisit",
                "message": f"Account {account_id} marked for revisit",
            })
        except Exception as e:
            logger.exception("[MCP] mark_account_revisit error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def create_revisit_signal(account_id: int, new_evidence: str) -> str:
        """Create a new signal for an account being revisited.

        Use this when you have new evidence for a previously contacted
        account that warrants another round of outreach.

        Args:
            account_id: The account to create a revisit signal for.
            new_evidence: The new evidence or observation.
        """
        try:
            from v2.services.signal_service import create_signal as _create

            # Auto-recommend campaign for revisit signals
            rec_campaign_id = None
            rec_campaign_reasoning = None
            try:
                from v2.services.campaign_service import recommend_campaign as _rec
                rec = _rec(signal_type='revisit', outreach_angle=new_evidence)
                rec_campaign_id = rec.get('campaign_id')
                rec_campaign_reasoning = rec.get('reasoning')
            except Exception:
                pass

            signal_id = _create(
                account_id=account_id,
                signal_description=f"Revisit: {new_evidence}",
                signal_type='revisit',
                evidence_type='manual',
                evidence_value=new_evidence,
                signal_source='cowork',
                recommended_campaign_id=rec_campaign_id,
                recommended_campaign_reasoning=rec_campaign_reasoning,
                created_by='mcp',
            )

            try:
                from v2.services.activity_service import log_activity
                log_activity(
                    event_type='revisit_signal_created',
                    entity_type='signal',
                    entity_id=signal_id,
                    details={
                        'account_id': account_id,
                        'evidence_preview': new_evidence[:200],
                    },
                    created_by='mcp',
                )
            except Exception:
                pass

            return _safe_json({
                "signal_id": signal_id,
                "account_id": account_id,
                "recommended_campaign_id": rec_campaign_id,
                "recommended_campaign_reasoning": rec_campaign_reasoning,
                "message": f"Revisit signal created (id={signal_id})",
            })
        except Exception as e:
            logger.exception("[MCP] create_revisit_signal error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Feedback & Activity Logs
    # ------------------------------------------------------------------

    @mcp.tool()
    def list_feedback_log(limit: int = 20) -> str:
        """List recent draft feedback/critique entries.

        Shows the history of draft regenerations and critique, useful for
        understanding writing preference trends.

        Args:
            limit: Max entries to return. Default 20.
        """
        try:
            from v2.services.feedback_service import get_recent_feedback
            entries = get_recent_feedback(limit=limit)
            return _safe_json({"feedback": entries, "count": len(entries)})
        except ImportError:
            return _safe_json({
                "feedback": [],
                "count": 0,
                "message": "Feedback service not yet available",
            })
        except Exception as e:
            logger.exception("[MCP] list_feedback_log error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def get_activity_log(entity_type: str = None, entity_id: int = None, limit: int = 20) -> str:
        """Get the activity audit log.

        Shows recent actions in the pipeline (signal creation, draft approval,
        enrollment, etc.). Optionally filter by entity type and ID.

        Args:
            entity_type: Optional filter (e.g. 'signal', 'account', 'prospect', 'draft').
            entity_id: Optional entity ID to filter by (requires entity_type).
            limit: Max entries to return. Default 20.
        """
        try:
            from v2.services.activity_service import get_recent_activity
            entries = get_recent_activity(
                limit=limit,
                entity_type=entity_type,
            )

            # If entity_id is specified, filter further
            if entity_id is not None and entity_type:
                entries = [
                    e for e in entries
                    if e.get('entity_id') == entity_id
                ]

            return _safe_json({"activity": entries, "count": len(entries)})
        except ImportError:
            return _safe_json({
                "activity": [],
                "count": 0,
                "message": "Activity service not yet available",
            })
        except Exception as e:
            logger.exception("[MCP] get_activity_log error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    @mcp.tool()
    def pipeline_analytics() -> str:
        """Get full pipeline conversion metrics.

        Returns the funnel from signals → prospects → drafts → enrollments
        with conversion rates at each stage, plus account status breakdown.
        """
        try:
            from v2.services.analytics_service import get_pipeline_summary, get_account_status_breakdown
            return _safe_json({
                "pipeline": get_pipeline_summary(),
                "accounts": get_account_status_breakdown(),
            })
        except Exception as e:
            logger.exception("[MCP] pipeline_analytics error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def campaign_analytics() -> str:
        """Get per-campaign performance metrics.

        Shows signal count, prospect count, enrollment count, and conversion
        rate for each campaign. Useful for identifying which campaigns perform best.
        """
        try:
            from v2.services.analytics_service import get_campaign_performance
            campaigns = get_campaign_performance()
            return _safe_json({"campaigns": campaigns, "count": len(campaigns)})
        except Exception as e:
            logger.exception("[MCP] campaign_analytics error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def draft_analytics() -> str:
        """Get draft quality and regeneration metrics.

        Shows total drafts, approval rate, and average regenerations per
        prospect. Useful for understanding writing quality trends.
        """
        try:
            from v2.services.analytics_service import get_draft_quality_metrics
            return _safe_json(get_draft_quality_metrics())
        except Exception as e:
            logger.exception("[MCP] draft_analytics error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Dedup
    # ------------------------------------------------------------------

    @mcp.tool()
    def find_duplicate_signals() -> str:
        """Find exact duplicate signals in the queue.

        Returns clusters of signals that share the same account + signal_type +
        evidence_value. Each cluster identifies which signal to keep (oldest)
        and which are duplicates.
        """
        try:
            from v2.services.dedup_service import find_exact_duplicates, get_dedup_summary
            clusters = find_exact_duplicates()
            summary = get_dedup_summary()
            return _safe_json({
                "summary": summary,
                "clusters": clusters,
                "total_clusters": len(clusters),
            })
        except Exception as e:
            logger.exception("[MCP] find_duplicate_signals error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def auto_clean_duplicates() -> str:
        """Automatically archive all exact duplicate signals.

        Keeps the oldest signal in each duplicate cluster and archives the rest.
        Returns how many clusters were processed and signals archived.
        """
        try:
            from v2.services.dedup_service import auto_archive_exact_duplicates
            result = auto_archive_exact_duplicates()
            return _safe_json(result)
        except Exception as e:
            logger.exception("[MCP] auto_clean_duplicates error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Bulk Enrollment
    # ------------------------------------------------------------------

    @mcp.tool()
    def bulk_enroll_prospects(prospect_ids: str) -> str:
        """Enroll multiple prospects into Apollo sequences at once.

        Returns per-prospect results showing who enrolled successfully,
        who failed and why, and who was skipped (already enrolled or DNC).

        Args:
            prospect_ids: Comma-separated prospect IDs (e.g. '12,34,56').
        """
        try:
            ids = [int(x.strip()) for x in prospect_ids.split(',') if x.strip()]
            if not ids:
                return _safe_json({"error": "No valid prospect IDs provided"})
            if len(ids) > 100:
                return _safe_json({"error": "Cannot enroll more than 100 prospects at once"})

            from v2.services.enrollment_service import bulk_enroll
            result = bulk_enroll(ids)
            return _safe_json(result)
        except ValueError:
            return _safe_json({"error": "prospect_ids must be comma-separated integers"})
        except Exception as e:
            logger.exception("[MCP] bulk_enroll_prospects error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Parity: Signal Management
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_signal_counts() -> str:
        """Get signal counts grouped by workflow status.

        Returns a dict like {"new": 12, "sequenced": 5, "revisit": 3, "noise": 1}.
        Useful for understanding queue load at a glance.
        """
        try:
            from v2.services.signal_service import get_signal_counts_by_status
            return _safe_json(get_signal_counts_by_status())
        except Exception as e:
            logger.exception("[MCP] get_signal_counts error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def get_signal_owners() -> str:
        """Get the list of distinct account owners who have signals in the queue.

        Useful for filtering the queue by owner.
        """
        try:
            from v2.services.signal_service import get_owners
            owners = get_owners()
            return _safe_json({"owners": owners, "count": len(owners)})
        except Exception as e:
            logger.exception("[MCP] get_signal_owners error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def update_signal_campaign(signal_id: int, campaign_id: int, reasoning: str = "") -> str:
        """Change the recommended campaign for a signal.

        Use this when the auto-recommended campaign isn't the best fit
        and you want to assign a different one.

        Args:
            signal_id: The signal to update.
            campaign_id: The new campaign ID to assign.
            reasoning: Optional explanation for why this campaign fits better.
        """
        try:
            from v2.services.signal_service import update_signal_campaign as _update
            ok = _update(signal_id, campaign_id, reasoning=reasoning)
            if not ok:
                return _safe_json({"error": f"Signal {signal_id} not found"})
            return _safe_json({
                "signal_id": signal_id,
                "campaign_id": campaign_id,
                "message": f"Campaign updated for signal {signal_id}",
            })
        except Exception as e:
            logger.exception("[MCP] update_signal_campaign error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def archive_signal(signal_id: int) -> str:
        """Archive a signal (soft delete from the queue).

        Use this to remove a signal from the active queue without deleting it.

        Args:
            signal_id: The signal to archive.
        """
        try:
            from v2.services.signal_service import archive_signal as _archive
            ok = _archive(signal_id)
            if not ok:
                return _safe_json({"error": f"Signal {signal_id} not found"})
            return _safe_json({"signal_id": signal_id, "status": "archived"})
        except Exception as e:
            logger.exception("[MCP] archive_signal error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Parity: Prospects
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_prospects(signal_id: int) -> str:
        """Get all prospects linked to a signal.

        Returns prospect details including enrollment status, email,
        and Apollo IDs.

        Args:
            signal_id: The signal to get prospects for.
        """
        try:
            from v2.services.prospect_service import get_prospects_for_signal
            prospects = get_prospects_for_signal(signal_id)
            return _safe_json({"prospects": prospects, "count": len(prospects)})
        except Exception as e:
            logger.exception("[MCP] get_prospects error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Parity: Drafts
    # ------------------------------------------------------------------

    @mcp.tool()
    def approve_all_drafts(prospect_id: int) -> str:
        """Approve all drafts for a prospect at once.

        Marks all generated/edited drafts as approved, making them ready
        for enrollment.

        Args:
            prospect_id: The prospect whose drafts to approve.
        """
        try:
            from v2.services.draft_service import approve_all_drafts as _approve_all
            drafts = _approve_all(prospect_id)
            return _safe_json({"drafts": drafts, "count": len(drafts)})
        except Exception as e:
            logger.exception("[MCP] approve_all_drafts error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Parity: Enrollment
    # ------------------------------------------------------------------

    @mcp.tool()
    def mark_sequence_complete(prospect_id: int) -> str:
        """Mark a prospect's Apollo sequence as complete.

        Triggers account-level rollup check — if ALL prospects for the
        account are complete, the account moves to 'revisit' status.

        Args:
            prospect_id: The prospect whose sequence finished.
        """
        try:
            from v2.services.enrollment_service import mark_sequence_complete as _mark
            result = _mark(prospect_id)
            if not result:
                return _safe_json({"error": f"Prospect {prospect_id} not found"})
            return _safe_json(result)
        except Exception as e:
            logger.exception("[MCP] mark_sequence_complete error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Parity: Campaigns & Writing Preferences
    # ------------------------------------------------------------------

    @mcp.tool()
    def list_campaigns() -> str:
        """List all available campaigns.

        Returns campaign names, types, and writing guidelines.
        Useful for choosing which campaign to assign to a signal.
        """
        try:
            from v2.db import db_connection, rows_to_dicts
            with db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, name, sequence_config, campaign_type, writing_guidelines
                    FROM campaigns ORDER BY name
                ''')
                campaigns = rows_to_dicts(cursor.fetchall())
            return _safe_json({"campaigns": campaigns, "count": len(campaigns)})
        except Exception as e:
            logger.exception("[MCP] list_campaigns error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def get_writing_preferences() -> str:
        """Get the org-wide writing preferences.

        Returns tone, banned phrases, preferred structure, CTA guidance,
        signoff guidance, and custom rules used for draft generation.
        """
        try:
            from v2.services.writing_prefs_service import get_writing_preferences as _get
            prefs = _get()
            return _safe_json({"preferences": prefs})
        except Exception as e:
            logger.exception("[MCP] get_writing_preferences error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def update_writing_preference(key: str, value: str) -> str:
        """Update a single writing preference.

        Valid keys: tone, banned_phrases, preferred_structure, cta_guidance,
        signoff_guidance, custom_rules.

        Args:
            key: The preference key to update.
            value: The new value.
        """
        try:
            from v2.services.writing_prefs_service import update_writing_preferences as _update
            ok = _update({key: value})
            if not ok:
                return _safe_json({"error": "Failed to update preference"})
            return _safe_json({"key": key, "message": f"Updated '{key}' preference"})
        except Exception as e:
            logger.exception("[MCP] update_writing_preference error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Per-BDR Writing Preferences
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_bdr_writing_preferences(user_email: str) -> str:
        """Get a BDR's personal writing preferences.

        Returns both the org-wide defaults and the BDR's personal
        overrides, plus the final merged result.

        Args:
            user_email: The BDR's email address.
        """
        try:
            from v2.services.writing_prefs_service import (
                get_writing_preferences as _get_org,
                get_bdr_preferences as _get_bdr,
                get_merged_preferences as _get_merged,
            )
            org = _get_org()
            bdr = _get_bdr(user_email)
            merged = _get_merged(user_email)
            return _safe_json({
                "user_email": user_email,
                "org_preferences": org,
                "personal_overrides": bdr,
                "merged_result": merged,
            })
        except Exception as e:
            logger.exception("[MCP] get_bdr_writing_preferences error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def update_bdr_writing_preference(user_email: str, key: str, value: str,
                                      override_mode: str = "add") -> str:
        """Set a personal writing preference for a BDR.

        This creates a personal override that layers on top of org-wide rules.
        Use this when a BDR wants to customize their email style.

        Args:
            user_email: The BDR's email address.
            key: Preference key. Common keys: 'banned_phrases', 'tone',
                 'signoff_guidance', 'cta_guidance', 'custom_rules'.
            value: The preference value.
            override_mode: How to apply the override:
                - 'add': Append to the org value (e.g., add more banned words)
                - 'replace': Fully replace the org value for this BDR
                - 'remove': Remove specific items from the org list (e.g., un-ban a word)
        """
        try:
            from v2.services.writing_prefs_service import update_bdr_preference as _update
            ok = _update(user_email, key, value, override_mode)
            if not ok:
                return _safe_json({"error": "Failed to update BDR preference"})
            return _safe_json({
                "user_email": user_email,
                "key": key,
                "override_mode": override_mode,
                "message": f"Updated personal '{key}' preference ({override_mode})",
            })
        except Exception as e:
            logger.exception("[MCP] update_bdr_writing_preference error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def delete_bdr_writing_preference(user_email: str, key: str,
                                      override_mode: str = "") -> str:
        """Remove a personal writing preference for a BDR.

        Removes the BDR's override for the specified key, reverting
        to the org-wide default for that preference.

        Args:
            user_email: The BDR's email address.
            key: The preference key to remove.
            override_mode: Specific mode to remove ('add', 'replace', 'remove').
                          If empty, removes all modes for that key.
        """
        try:
            from v2.services.writing_prefs_service import delete_bdr_preference as _delete
            mode = override_mode if override_mode else None
            ok = _delete(user_email, key, mode)
            return _safe_json({
                "user_email": user_email,
                "key": key,
                "message": f"Removed personal '{key}' preference override",
            })
        except Exception as e:
            logger.exception("[MCP] delete_bdr_writing_preference error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Parity: Account Status (full set)
    # ------------------------------------------------------------------

    @mcp.tool()
    def mark_account_sequenced(account_id: int) -> str:
        """Mark an account as sequenced (at least one prospect enrolled).

        Cascades: all 'new' signals for this account move to 'actioned'.

        Args:
            account_id: The account to mark as sequenced.
        """
        try:
            from v2.services.account_service import mark_account_sequenced as _mark
            ok = _mark(account_id)
            if not ok:
                return _safe_json({"error": f"Failed to mark account {account_id} as sequenced"})
            return _safe_json({
                "account_id": account_id,
                "status": "sequenced",
                "message": f"Account {account_id} marked as sequenced",
            })
        except Exception as e:
            logger.exception("[MCP] mark_account_sequenced error")
            return _safe_json({"error": str(e)})

    @mcp.tool()
    def reset_account_status(account_id: int) -> str:
        """Reset an account back to 'new' status.

        Use this to undo a noise/revisit/sequenced designation.
        No signal cascade — signals keep their current status.

        Args:
            account_id: The account to reset.
        """
        try:
            from v2.services.account_service import update_account_status
            ok = update_account_status(account_id, 'new')
            if not ok:
                return _safe_json({"error": f"Failed to reset account {account_id}"})
            return _safe_json({
                "account_id": account_id,
                "status": "new",
                "message": f"Account {account_id} reset to new",
            })
        except Exception as e:
            logger.exception("[MCP] reset_account_status error")
            return _safe_json({"error": str(e)})

    logger.info("[MCP] Registered %d v2 tools", 35)
