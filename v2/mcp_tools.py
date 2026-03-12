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
        """List intent signals in the queue, filtered by status and/or owner.

        Returns signals sorted newest-first with account info. Use this to see
        what signals need attention.

        Args:
            status: Filter by signal status ('new', 'actioned', 'archived'). Default 'new'.
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
    def create_signal(account_name: str, signal_description: str, signal_type: str = None, evidence: str = None) -> str:
        """Create a new intent signal for an account.

        Finds or creates the account by name, then creates the signal.
        Use this when you discover a new localization intent signal.

        Args:
            account_name: Company name (will find existing or create new account).
            signal_description: What was observed (e.g. 'Added react-i18next to package.json').
            signal_type: Optional signal category (e.g. 'dependency_injection', 'rfc_discussion').
            evidence: Optional raw evidence text or URL.
        """
        try:
            from v2.services.account_service import find_or_create_account
            from v2.services.signal_service import create_signal as _create

            account_id = find_or_create_account(account_name)
            signal_id = _create(
                account_id=account_id,
                signal_description=signal_description,
                signal_type=signal_type,
                evidence_type='manual',
                evidence_value=evidence,
                signal_source='manual_entry',
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
    def find_prospects(signal_id: int, titles: str = "VP Engineering,Head of Product", seniorities: str = "vp,director") -> str:
        """Search Apollo for prospects matching the signal's account.

        Finds people at the account's domain with the given titles/seniorities.
        Returns contact details including email and LinkedIn URL.

        Args:
            signal_id: The signal whose account to search.
            titles: Comma-separated job titles to search for.
            seniorities: Comma-separated seniority levels (e.g. 'vp,director,c_suite').
        """
        try:
            from v2.services.signal_service import get_signal
            from v2.services.account_service import get_account_domain

            signal = get_signal(signal_id)
            if not signal:
                return _safe_json({"error": f"Signal {signal_id} not found"})

            account_id = signal['account_id']
            domain = get_account_domain(account_id)
            if not domain:
                return _safe_json({"error": "Account has no website/domain configured"})

            title_list = [t.strip() for t in titles.split(',') if t.strip()]
            seniority_list = [s.strip() for s in seniorities.split(',') if s.strip()]

            from apollo_pipeline import apollo_api_call

            search_body = {
                'q_organization_domains': domain,
                'person_titles': title_list,
                'page': 1,
                'per_page': 25,
            }
            if seniority_list:
                search_body['person_seniorities'] = seniority_list

            resp = apollo_api_call(
                'post',
                'https://api.apollo.io/v1/mixed_people/search',
                json=search_body,
            )

            if resp.status_code != 200:
                return _safe_json({
                    "error": f"Apollo search failed with status {resp.status_code}",
                    "detail": resp.text[:300],
                })

            people = resp.json().get('people', [])
            results = []
            seen = set()
            for person in people:
                email = (person.get('email') or '').lower()
                if email and email in seen:
                    continue
                if email:
                    seen.add(email)
                results.append({
                    'full_name': person.get('name', ''),
                    'first_name': person.get('first_name', ''),
                    'last_name': person.get('last_name', ''),
                    'title': person.get('title', ''),
                    'email': person.get('email', ''),
                    'email_verified': person.get('email_status') == 'verified',
                    'linkedin_url': person.get('linkedin_url', ''),
                    'apollo_person_id': person.get('id', ''),
                })

            return _safe_json({
                "people": results,
                "total": len(results),
                "domain": domain,
                "signal_id": signal_id,
                "account_id": account_id,
            })
        except Exception as e:
            logger.exception("[MCP] find_prospects error")
            return _safe_json({"error": str(e)})

    # ------------------------------------------------------------------
    # Draft Generation & Management
    # ------------------------------------------------------------------

    @mcp.tool()
    def generate_draft_sequence(prospect_id: int, signal_id: int, campaign_id: int) -> str:
        """Generate a multi-step email draft sequence for a prospect.

        Creates 3 drafts (initial outreach, follow-up, breakup) using the
        signal context and campaign guidelines. Uses AI when available,
        falls back to templates.

        Args:
            prospect_id: The prospect to generate drafts for.
            signal_id: The intent signal providing context.
            campaign_id: The campaign with writing guidelines.
        """
        try:
            from v2.services.draft_service import generate_drafts
            drafts = generate_drafts(prospect_id, signal_id, campaign_id)
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

            signal_id = _create(
                account_id=account_id,
                signal_description=f"Revisit: {new_evidence}",
                signal_type='revisit',
                evidence_type='manual',
                evidence_value=new_evidence,
                signal_source='cowork',
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

    logger.info("[MCP] Registered %d v2 tools", 15)
