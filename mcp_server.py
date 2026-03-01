#!/usr/bin/env python3
"""
MCP Server for GitHub Dossier — Sales Intelligence Tool.

Exposes GitHub org scanning, report retrieval, account management,
website analysis, and pipeline tools for Claude Code integration.

Usage:
    python mcp_server.py              # stdio transport (for Claude Code)
    mcp dev mcp_server.py             # interactive dev/test mode
"""

import json
import sys
import os
import time
from typing import Optional, List

# Ensure project root is on sys.path so we can import existing modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP, Context

# Import existing modules (these trigger load_dotenv and config loading)
from database import (
    init_db,
    get_report,
    search_reports,
    get_recent_reports,
    get_all_accounts,
    get_account_by_company_case_insensitive,
    get_tier_counts,
    get_signals_for_report,
    save_report,
    save_signals,
    update_account_status,
    add_account_to_tier_0,
)
from monitors.scanner import deep_scan_generator
from monitors.web_analyzer import analyze_website_technical
from ai_summary import generate_analysis

# Initialize database (creates tables if needed)
init_db()

# Create MCP server
mcp = FastMCP("dossier_mcp")


# ---------------------------------------------------------------------------
# Pydantic Input Models
# ---------------------------------------------------------------------------

class ScanCompanyInput(BaseModel):
    """Input for scanning a company's GitHub presence."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    company_name: str = Field(
        ...,
        description="Company name to scan (e.g., 'Stripe', 'Shopify')",
        min_length=1,
        max_length=200,
    )
    github_org: Optional[str] = Field(
        default=None,
        description="GitHub org login to use directly, skipping discovery (e.g., 'stripe')",
    )


class GetReportInput(BaseModel):
    """Input for retrieving a scan report."""
    model_config = ConfigDict(extra="forbid")

    report_id: int = Field(..., description="Report ID to retrieve", ge=1)


class ListAccountsInput(BaseModel):
    """Input for listing monitored accounts."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    page: Optional[int] = Field(default=1, description="Page number (1-indexed)", ge=1)
    limit: Optional[int] = Field(default=25, description="Results per page", ge=1, le=100)
    tier: Optional[int] = Field(
        default=None,
        description="Filter by tier: 0=Tracking, 1=Thinking, 2=Preparing, 3=Dimmed, 4=Invalid",
        ge=0,
        le=4,
    )
    search: Optional[str] = Field(
        default=None,
        description="Search by company name (partial match)",
        min_length=1,
    )


class SearchReportsInput(BaseModel):
    """Input for searching scan reports."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Company name to search for (partial match)",
        min_length=1,
        max_length=200,
    )


class AnalyzeWebsiteInput(BaseModel):
    """Input for analyzing a website's localization readiness."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    url: str = Field(
        ...,
        description="Website URL to analyze (e.g., 'https://stripe.com')",
        min_length=4,
    )


class GetAccountInput(BaseModel):
    """Input for getting a single account."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    company_name: str = Field(
        ...,
        description="Company name to look up (case-insensitive)",
        min_length=1,
    )


class ListRecentReportsInput(BaseModel):
    """Input for listing recent reports."""
    model_config = ConfigDict(extra="forbid")

    limit: Optional[int] = Field(
        default=20,
        description="Maximum number of reports to return",
        ge=1,
        le=100,
    )


class AddAccountInput(BaseModel):
    """Input for adding a company to the monitoring pipeline."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    company_name: str = Field(
        ...,
        description="Company name",
        min_length=1,
        max_length=200,
    )
    github_org: str = Field(
        ...,
        description="GitHub organization login (e.g., 'stripe')",
        min_length=1,
        max_length=100,
    )
    website: Optional[str] = Field(default=None, description="Company website URL")
    annual_revenue: Optional[str] = Field(
        default=None,
        description="Annual revenue string (e.g., '$50M', '$4.6B')",
    )


class GetSignalsInput(BaseModel):
    """Input for getting signals from a report."""
    model_config = ConfigDict(extra="forbid")

    report_id: int = Field(..., description="Report ID to get signals for", ge=1)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _json_response(data) -> str:
    """Serialize to JSON with datetime handling."""
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="dossier_scan_company",
    annotations={
        "title": "Scan Company GitHub Presence",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dossier_scan_company(params: ScanCompanyInput, ctx: Context) -> str:
    """Run a full 3-Signal Intent Scan on a company's GitHub presence.

    Scans for pre-launch internationalization signals:
    - RFC & Discussion (Thinking Phase)
    - Dependency Injection (Preparing Phase)
    - Ghost Branch (Active Phase)

    Then generates AI sales intelligence with executive summary, pain points,
    opportunity score, and a cold email draft.

    Takes 30-120 seconds depending on org size. Progress is reported
    incrementally.

    Args:
        params (ScanCompanyInput): Validated input with:
            - company_name (str): Company name to scan
            - github_org (Optional[str]): GitHub org login to skip discovery

    Returns:
        str: JSON containing report_id, signals_found, intent_score,
             executive_summary, recommended_approach, cold_email_draft,
             and scan duration.
    """
    start_time = time.time()
    scan_data = None
    analysis_data = None
    step = 0

    # Look up existing account for pre-linked org and last scan timestamp
    account = get_account_by_company_case_insensitive(params.company_name)
    last_scanned_at = account.get("last_scanned_at") if account else None
    github_org = params.github_org or (account.get("github_org") if account else None)

    # Phase 1: Deep scan
    await ctx.report_progress(0, 100)
    await ctx.info(f"Starting 3-Signal scan for {params.company_name}...")

    for message in deep_scan_generator(params.company_name, last_scanned_at, github_org):
        step += 1

        if "data: LOG:" in message:
            log_msg = message.split("data: LOG:", 1)[1].strip().replace("\n", "")
            if step % 5 == 0:
                await ctx.report_progress(min(step, 60), 100)
                await ctx.info(log_msg)

        elif "data: ERROR:" in message:
            error_msg = message.split("data: ERROR:", 1)[1].strip().replace("\n", "")
            return _json_response({"error": error_msg, "company": params.company_name})

        elif "SCAN_COMPLETE:" in message:
            json_str = message.split("SCAN_COMPLETE:", 1)[1].strip()
            if json_str.startswith("data: "):
                json_str = json_str[6:]
            scan_data = json.loads(json_str)

    if not scan_data:
        return _json_response({"error": "No scan data generated", "company": params.company_name})

    # Phase 2: AI analysis
    await ctx.report_progress(65, 100)
    await ctx.info("Generating AI sales intelligence...")

    try:
        for message in generate_analysis(scan_data):
            if "ANALYSIS_COMPLETE:" in message:
                json_str = message.split("ANALYSIS_COMPLETE:", 1)[1].strip()
                if json_str.startswith("data: "):
                    json_str = json_str[6:]
                analysis_data = json.loads(json_str)
    except Exception as e:
        analysis_data = {"error": str(e), "executive_summary": "Analysis failed"}

    # Phase 3: Save to database
    await ctx.report_progress(85, 100)
    await ctx.info("Saving report...")

    duration = time.time() - start_time
    try:
        report_id = save_report(
            company_name=params.company_name,
            github_org=scan_data.get("org_login", ""),
            scan_data=scan_data,
            ai_analysis=analysis_data or {},
            scan_duration=duration,
        )
    except Exception as e:
        return _json_response({"error": f"Failed to save report: {e}", "company": params.company_name})

    # Save signals
    signals = scan_data.get("signals", [])
    if signals and report_id:
        try:
            save_signals(report_id, params.company_name, signals)
        except Exception:
            pass  # Non-fatal

    # Update account status
    try:
        update_account_status(scan_data, report_id)
    except Exception:
        pass  # Non-fatal

    await ctx.report_progress(100, 100)

    analysis = analysis_data or {}
    return _json_response({
        "report_id": report_id,
        "company": params.company_name,
        "github_org": scan_data.get("org_login"),
        "signals_found": len(signals),
        "intent_score": scan_data.get("intent_score", 0),
        "duration_seconds": round(duration, 1),
        "executive_summary": analysis.get("executive_summary", ""),
        "recommended_approach": analysis.get("recommended_approach", ""),
        "cold_email_draft": analysis.get("cold_email_draft", {}),
    })


@mcp.tool(
    name="dossier_get_report",
    annotations={
        "title": "Get Scan Report",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_get_report(params: GetReportInput) -> str:
    """Retrieve a completed scan report by ID.

    Returns the full report including scan data, AI analysis, signals,
    and firmographic data (website, revenue).

    Args:
        params (GetReportInput): report_id (int) to retrieve.

    Returns:
        str: JSON with report data or error message.
    """
    report = get_report(params.report_id)
    if not report:
        return _json_response({"error": f"Report {params.report_id} not found"})

    # Parse JSON strings for readability
    for key in ("scan_data", "ai_analysis"):
        if isinstance(report.get(key), str):
            try:
                report[key] = json.loads(report[key])
            except (json.JSONDecodeError, TypeError):
                pass

    return _json_response(report)


@mcp.tool(
    name="dossier_list_accounts",
    annotations={
        "title": "List Monitored Accounts",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_list_accounts(params: ListAccountsInput) -> str:
    """List monitored accounts with pagination and filtering.

    Returns accounts sorted by tier priority (Preparing first, then
    Thinking, Tracking, Dimmed, Invalid).

    Args:
        params (ListAccountsInput): Pagination and filter options:
            - page (int): Page number, default 1
            - limit (int): Results per page, default 25
            - tier (Optional[int]): Filter by tier (0-4)
            - search (Optional[str]): Search by company name

    Returns:
        str: JSON with accounts list, total_items, total_pages, current_page.
    """
    tier_filter = [params.tier] if params.tier is not None else None
    result = get_all_accounts(
        page=params.page,
        limit=params.limit,
        tier_filter=tier_filter,
        search_query=params.search,
    )
    return _json_response(result)


@mcp.tool(
    name="dossier_search_reports",
    annotations={
        "title": "Search Scan Reports",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_search_reports(params: SearchReportsInput) -> str:
    """Search scan reports by company name (partial match).

    Args:
        params (SearchReportsInput): query (str) to search for.

    Returns:
        str: JSON list of matching report summaries (id, company_name,
             github_org, signals_found, created_at).
    """
    results = search_reports(params.query)
    if not results:
        return _json_response({"message": f"No reports found matching '{params.query}'"})
    return _json_response(results)


@mcp.tool(
    name="dossier_analyze_website",
    annotations={
        "title": "Analyze Website Localization",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dossier_analyze_website(params: AnalyzeWebsiteInput) -> str:
    """Analyze a website for localization readiness.

    Checks for hreflang tags, language switchers, i18n JS libraries,
    locale URL patterns, and translation platform usage.

    Args:
        params (AnalyzeWebsiteInput): url (str) to analyze.

    Returns:
        str: JSON with localization_score (0-100), grade (A-F),
             tech_stack, hreflang data, and recommendations.
    """
    try:
        result = analyze_website_technical(params.url)
        return _json_response(result)
    except Exception as e:
        return _json_response({"error": f"Website analysis failed: {e}", "url": params.url})


@mcp.tool(
    name="dossier_get_account",
    annotations={
        "title": "Get Account Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_get_account(params: GetAccountInput) -> str:
    """Get account details for a company (case-insensitive lookup).

    Returns tier, status, GitHub org, website, revenue, scan history,
    and evidence summary.

    Args:
        params (GetAccountInput): company_name (str) to look up.

    Returns:
        str: JSON with account data or error if not found.
    """
    account = get_account_by_company_case_insensitive(params.company_name)
    if not account:
        return _json_response({
            "error": f"No account found for '{params.company_name}'. "
                     "Use dossier_add_account to add it first.",
        })
    return _json_response(dict(account))


@mcp.tool(
    name="dossier_get_pipeline_summary",
    annotations={
        "title": "Get Pipeline Summary",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_get_pipeline_summary() -> str:
    """Get account counts by tier for the monitoring pipeline.

    Returns counts for each tier:
    - Tier 0: Tracking (newly added, not yet scanned)
    - Tier 1: Thinking (RFC/discussion signals detected)
    - Tier 2: Preparing (dependency/branch signals detected)
    - Tier 3: Dimmed (low confidence or stale)
    - Tier 4: Invalid (no GitHub org found or scan errors)

    Returns:
        str: JSON with tier counts and total.
    """
    counts = get_tier_counts()
    total = sum(counts.values())
    return _json_response({
        "tier_counts": counts,
        "total_accounts": total,
        "tier_labels": {
            "0": "Tracking",
            "1": "Thinking",
            "2": "Preparing",
            "3": "Dimmed",
            "4": "Invalid",
        },
    })


@mcp.tool(
    name="dossier_list_recent_reports",
    annotations={
        "title": "List Recent Reports",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_list_recent_reports(params: ListRecentReportsInput) -> str:
    """Get the most recent scan reports, deduplicated by company.

    Only shows the latest report for each company.

    Args:
        params (ListRecentReportsInput): limit (int) max reports, default 20.

    Returns:
        str: JSON list of report summaries (id, company_name, github_org,
             signals_found, repos_scanned, created_at).
    """
    results = get_recent_reports(limit=params.limit)
    return _json_response(results)


@mcp.tool(
    name="dossier_add_account",
    annotations={
        "title": "Add Account to Pipeline",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def dossier_add_account(params: AddAccountInput) -> str:
    """Add a new company to the monitoring pipeline at Tier 0 (Tracking).

    If the account already exists, updates GitHub org and metadata.

    Args:
        params (AddAccountInput):
            - company_name (str): Company name
            - github_org (str): GitHub organization login
            - website (Optional[str]): Company website
            - annual_revenue (Optional[str]): Revenue string

    Returns:
        str: JSON with account creation/update result.
    """
    result = add_account_to_tier_0(
        company_name=params.company_name,
        github_org=params.github_org,
        website=params.website,
        annual_revenue=params.annual_revenue,
    )
    return _json_response(result)


@mcp.tool(
    name="dossier_get_signals",
    annotations={
        "title": "Get Report Signals",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_get_signals(params: GetSignalsInput) -> str:
    """Get all i18n signals detected in a scan report.

    Signal types include: RFC Discussion, Dependency Injection,
    Ghost Branch, and various enhanced heuristics.

    Args:
        params (GetSignalsInput): report_id (int) to get signals for.

    Returns:
        str: JSON list of signals with type, description, file_path, timestamp.
    """
    signals = get_signals_for_report(params.report_id)
    if not signals:
        return _json_response({
            "message": f"No signals found for report {params.report_id}. "
                       "The report may not exist or the scan found no signals.",
        })
    return _json_response(signals)


# ---------------------------------------------------------------------------
# Apollo Enrollment Tools
# ---------------------------------------------------------------------------

import requests as _requests

_APOLLO_BASE = "https://api.apollo.io"


def _apollo_headers() -> dict:
    """Return Apollo API headers using APOLLO_API_KEY env var."""
    key = os.environ.get("APOLLO_API_KEY", "")
    if not key:
        raise ValueError(
            "APOLLO_API_KEY is not configured. "
            "Set it as an environment variable or in Settings."
        )
    return {"X-Api-Key": key, "Content-Type": "application/json"}


class ApolloSearchPeopleInput(BaseModel):
    """Input for searching people in Apollo."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    domain: str = Field(..., description="Company domain to search (e.g., 'stripe.com')")
    titles: Optional[List[str]] = Field(
        default=None,
        description="Job titles to filter by (e.g., ['VP Engineering', 'CTO'])",
    )
    seniorities: Optional[List[str]] = Field(
        default=None,
        description="Seniority levels (e.g., ['vp', 'director', 'c_suite', 'manager'])",
    )
    per_page: Optional[int] = Field(default=25, description="Results per page (max 100)", ge=1, le=100)
    page: Optional[int] = Field(default=1, description="Page number", ge=1)


class ApolloSearchSequencesInput(BaseModel):
    """Input for searching Apollo sequences."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: Optional[str] = Field(default=None, description="Sequence name keyword filter")
    page: Optional[int] = Field(default=1, description="Page number", ge=1)
    per_page: Optional[int] = Field(default=25, description="Results per page", ge=1, le=100)


class ApolloCreateContactInput(BaseModel):
    """Input for creating an Apollo contact."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    first_name: str = Field(..., description="Contact first name")
    last_name: str = Field(..., description="Contact last name")
    email: str = Field(..., description="Contact email address")
    organization_name: Optional[str] = Field(default=None, description="Company name")
    title: Optional[str] = Field(default=None, description="Job title")


class ApolloEnrollContactInput(BaseModel):
    """Input for enrolling a contact into an Apollo sequence."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    contact_id: str = Field(..., description="Apollo contact ID to enroll")
    sequence_id: str = Field(..., description="Apollo sequence ID to enroll into")
    sender_email_account_id: Optional[str] = Field(
        default=None,
        description="Apollo email account ID to send from. If omitted, uses first active account.",
    )


class ApolloListEmailAccountsInput(BaseModel):
    """Input for listing email accounts (no params needed)."""
    model_config = ConfigDict(extra="forbid")


class ApolloBatchEnrollInput(BaseModel):
    """Input for batch-enrolling multiple contacts into a sequence."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    contact_ids: List[str] = Field(..., description="List of Apollo contact IDs to enroll", min_length=1)
    sequence_id: str = Field(..., description="Apollo sequence ID")
    sender_email_account_id: Optional[str] = Field(
        default=None,
        description="Apollo email account ID to send from",
    )


@mcp.tool(
    name="apollo_search_people",
    annotations={
        "title": "Search People in Apollo",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def apollo_search_people(params: ApolloSearchPeopleInput) -> str:
    """Search for people at a company in the Apollo database.

    Finds contacts by domain, job titles, and seniority levels.
    Does NOT return email addresses — use apollo_create_contact to enrich.

    Args:
        params: domain, titles, seniorities, per_page, page

    Returns:
        str: JSON with people results and pagination info.
    """
    try:
        headers = _apollo_headers()
    except ValueError as e:
        return _json_response({"error": str(e)})

    payload: dict = {
        "q_organization_domains_list": [params.domain],
        "per_page": params.per_page,
        "page": params.page,
    }
    if params.titles:
        payload["person_titles"] = params.titles
    if params.seniorities:
        payload["person_seniorities"] = params.seniorities

    try:
        resp = _requests.post(
            f"{_APOLLO_BASE}/v1/mixed_people/search",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            return _json_response({"error": f"Apollo returned {resp.status_code}", "body": resp.text[:500]})
        data = resp.json()
        people = data.get("people", [])
        pagination = data.get("pagination", {})
        return _json_response({
            "total": pagination.get("total_entries", len(people)),
            "page": pagination.get("page", params.page),
            "per_page": params.per_page,
            "people": [
                {
                    "id": p.get("id"),
                    "first_name": p.get("first_name"),
                    "last_name": p.get("last_name"),
                    "title": p.get("title"),
                    "seniority": p.get("seniority"),
                    "email": p.get("email"),
                    "linkedin_url": p.get("linkedin_url"),
                    "organization_name": (p.get("organization") or {}).get("name"),
                }
                for p in people
            ],
        })
    except Exception as e:
        return _json_response({"error": f"Apollo search failed: {e}"})


@mcp.tool(
    name="apollo_search_sequences",
    annotations={
        "title": "Search Apollo Sequences",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def apollo_search_sequences(params: ApolloSearchSequencesInput) -> str:
    """Search for email sequences in Apollo.

    Args:
        params: name filter, page, per_page

    Returns:
        str: JSON list of sequences with id, name, active status, num_steps.
    """
    try:
        headers = _apollo_headers()
    except ValueError as e:
        return _json_response({"error": str(e)})

    payload: dict = {"page": params.page, "per_page": params.per_page}
    if params.name:
        payload["q_name"] = params.name

    try:
        resp = _requests.post(
            f"{_APOLLO_BASE}/api/v1/emailer_campaigns/search",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            return _json_response({"error": f"Apollo returned {resp.status_code}"})
        data = resp.json()
        campaigns = data.get("emailer_campaigns", [])
        return _json_response({
            "total": data.get("pagination", {}).get("total_entries", len(campaigns)),
            "sequences": [
                {
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "active": c.get("active", False),
                    "num_steps": c.get("num_steps", 0),
                    "created_at": c.get("created_at"),
                }
                for c in campaigns
            ],
        })
    except Exception as e:
        return _json_response({"error": f"Sequence search failed: {e}"})


@mcp.tool(
    name="apollo_list_email_accounts",
    annotations={
        "title": "List Apollo Email Accounts",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def apollo_list_email_accounts(params: ApolloListEmailAccountsInput) -> str:
    """List linked email accounts in Apollo for sending sequences.

    Returns:
        str: JSON list of email accounts with id, email, active status.
    """
    try:
        headers = _apollo_headers()
    except ValueError as e:
        return _json_response({"error": str(e)})

    try:
        resp = _requests.get(
            f"{_APOLLO_BASE}/v1/email_accounts",
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            return _json_response({"error": f"Apollo returned {resp.status_code}"})
        data = resp.json()
        accounts = data.get("email_accounts", [])
        return _json_response({
            "email_accounts": [
                {
                    "id": a.get("id"),
                    "email": a.get("email"),
                    "active": a.get("active", False),
                    "type": a.get("type"),
                    "user_id": a.get("user_id"),
                }
                for a in accounts
            ],
        })
    except Exception as e:
        return _json_response({"error": f"Failed to list email accounts: {e}"})


@mcp.tool(
    name="apollo_create_contact",
    annotations={
        "title": "Create Apollo Contact",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def apollo_create_contact(params: ApolloCreateContactInput) -> str:
    """Create a new contact in Apollo (or return existing if email matches).

    Args:
        params: first_name, last_name, email, organization_name, title

    Returns:
        str: JSON with created/existing contact id and details.
    """
    try:
        headers = _apollo_headers()
    except ValueError as e:
        return _json_response({"error": str(e)})

    payload: dict = {
        "first_name": params.first_name,
        "last_name": params.last_name,
        "email": params.email,
    }
    if params.organization_name:
        payload["organization_name"] = params.organization_name
    if params.title:
        payload["title"] = params.title

    try:
        resp = _requests.post(
            f"{_APOLLO_BASE}/v1/contacts",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code not in (200, 201):
            return _json_response({"error": f"Apollo returned {resp.status_code}", "body": resp.text[:500]})
        data = resp.json()
        contact = data.get("contact", {})
        return _json_response({
            "contact_id": contact.get("id"),
            "first_name": contact.get("first_name"),
            "last_name": contact.get("last_name"),
            "email": contact.get("email"),
            "title": contact.get("title"),
            "organization_name": contact.get("organization_name"),
        })
    except Exception as e:
        return _json_response({"error": f"Contact creation failed: {e}"})


@mcp.tool(
    name="apollo_enroll_contact",
    annotations={
        "title": "Enroll Contact in Sequence",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def apollo_enroll_contact(params: ApolloEnrollContactInput) -> str:
    """Enroll a single contact into an Apollo email sequence.

    Args:
        params: contact_id, sequence_id, sender_email_account_id (optional)

    Returns:
        str: JSON with enrollment result.
    """
    try:
        headers = _apollo_headers()
    except ValueError as e:
        return _json_response({"error": str(e)})

    # If no sender specified, fetch first active email account
    sender_id = params.sender_email_account_id
    if not sender_id:
        try:
            acct_resp = _requests.get(f"{_APOLLO_BASE}/v1/email_accounts", headers=headers, timeout=10)
            if acct_resp.status_code == 200:
                accounts = acct_resp.json().get("email_accounts", [])
                active = [a for a in accounts if a.get("active")]
                if active:
                    sender_id = active[0]["id"]
        except Exception:
            pass
    if not sender_id:
        return _json_response({"error": "No sender email account found. Provide sender_email_account_id."})

    payload = {
        "contact_ids": [params.contact_id],
        "emailer_campaign_id": params.sequence_id,
        "send_email_from_email_account_id": sender_id,
        "sequence_active_in_other_campaigns": False,
        "sequence_no_email": False,
    }

    try:
        resp = _requests.post(
            f"{_APOLLO_BASE}/api/v1/emailer_campaigns/{params.sequence_id}/add_contact_ids",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code not in (200, 201):
            return _json_response({"error": f"Enrollment failed: {resp.status_code}", "body": resp.text[:500]})
        data = resp.json()
        return _json_response({
            "enrolled": True,
            "contact_id": params.contact_id,
            "sequence_id": params.sequence_id,
            "sender_email_account_id": sender_id,
            "contacts_added": data.get("contacts", []),
        })
    except Exception as e:
        return _json_response({"error": f"Enrollment failed: {e}"})


@mcp.tool(
    name="apollo_batch_enroll",
    annotations={
        "title": "Batch Enroll Contacts in Sequence",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def apollo_batch_enroll(params: ApolloBatchEnrollInput) -> str:
    """Enroll multiple contacts into an Apollo email sequence at once.

    Args:
        params: contact_ids (list), sequence_id, sender_email_account_id (optional)

    Returns:
        str: JSON with batch enrollment result.
    """
    try:
        headers = _apollo_headers()
    except ValueError as e:
        return _json_response({"error": str(e)})

    sender_id = params.sender_email_account_id
    if not sender_id:
        try:
            acct_resp = _requests.get(f"{_APOLLO_BASE}/v1/email_accounts", headers=headers, timeout=10)
            if acct_resp.status_code == 200:
                accounts = acct_resp.json().get("email_accounts", [])
                active = [a for a in accounts if a.get("active")]
                if active:
                    sender_id = active[0]["id"]
        except Exception:
            pass
    if not sender_id:
        return _json_response({"error": "No sender email account found. Provide sender_email_account_id."})

    payload = {
        "contact_ids": params.contact_ids,
        "emailer_campaign_id": params.sequence_id,
        "send_email_from_email_account_id": sender_id,
        "sequence_active_in_other_campaigns": False,
        "sequence_no_email": False,
    }

    try:
        resp = _requests.post(
            f"{_APOLLO_BASE}/api/v1/emailer_campaigns/{params.sequence_id}/add_contact_ids",
            json=payload,
            headers=headers,
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            return _json_response({"error": f"Batch enrollment failed: {resp.status_code}", "body": resp.text[:500]})
        data = resp.json()
        return _json_response({
            "enrolled": True,
            "contact_count": len(params.contact_ids),
            "sequence_id": params.sequence_id,
            "sender_email_account_id": sender_id,
            "contacts_added": data.get("contacts", []),
        })
    except Exception as e:
        return _json_response({"error": f"Batch enrollment failed: {e}"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "sse":
        # HTTP/SSE transport for Replit and remote clients
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "5001"))
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport="stdio")
