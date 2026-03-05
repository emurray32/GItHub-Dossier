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
import logging
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
    get_all_campaigns,
    get_campaign,
    get_campaign_personas,
    get_contributors_by_company,
    get_contributor_by_id,
    get_enrollment_batches_for_campaign,
    get_enrollment_contacts,
    get_enrollment_batch_summary,
    create_campaign,
    create_campaign_persona,
)
from monitors.scanner import deep_scan_generator
from monitors.web_analyzer import analyze_website_technical
from ai_summary import generate_analysis

# Initialize database (creates tables if needed)
init_db()

# Create MCP server
mcp = FastMCP("dossier_mcp")


# ---------------------------------------------------------------------------
# MCP Resource: Cold Outreach Skill
# ---------------------------------------------------------------------------

_SKILL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".agent", "skills", "cold-outreach", "SKILL.md",
)


@mcp.resource("dossier://skills/cold-outreach")
def cold_outreach_skill() -> str:
    """Cold outreach writing rules and BDR workflow for Phrase."""
    try:
        with open(_SKILL_PATH, "r") as f:
            return f.read()
    except FileNotFoundError:
        return "Cold outreach skill file not found."


# ---------------------------------------------------------------------------
# MCP Prompt: Write Outreach for Account
# ---------------------------------------------------------------------------

@mcp.prompt()
def write_outreach(company_name: str) -> str:
    """Start the cold email writing workflow for a target account.

    Gathers intent signals, finds prospects, and guides you through
    writing a personalized email sequence one email at a time.
    """
    return f"""The BDR wants to write cold outreach emails for **{company_name}**.

Follow this workflow:

1. **Gather context** — Call these tools in parallel:
   - `dossier_get_account_signals` for "{company_name}"
   - `dossier_get_contributors` for "{company_name}" with has_email=true
   - `dossier_get_account` for "{company_name}"

2. **Brief the BDR** — Show a concise 3-4 line summary:
   - Company name + maturity level
   - Strongest intent signal (1-2 sentences, e.g., "Added react-i18next to main-app repo 3 weeks ago")
   - Top prospects with email (names + titles)

3. **Ask who to target** — "Who do you want to reach out to? Or should I pick the best match?"

4. **Write Email 1** — Present TWO versions (A and B, different angles). Follow the cold outreach skill rules exactly (read `dossier://skills/cold-outreach` for writing rules).

5. **Iterate one at a time** — After BDR picks/edits Email 1, write Email 2 (one version only). Get approval. Then Email 3. Then Email 4. Each email must use a different angle and build on the sequence arc:
   - Email 1: Hook + value prop (strongest signal)
   - Email 2: Different angle (different signal or pain point)
   - Email 3: Lighter touch (social proof or quick insight)
   - Email 4: Breakup (final value add, graceful close)

6. **Enroll** — After all emails are approved, ask: "Ready to enroll [name] into the Apollo sequence?" If yes, call `dossier_enroll_contributor`.

IMPORTANT: Read the cold outreach skill resource for email formatting rules, Apollo dynamic variables, persona adaptation, and Phrase messaging guidelines."""


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


class ListCampaignsInput(BaseModel):
    """Input for listing campaigns."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: Optional[str] = Field(
        default=None,
        description="Filter by status: 'draft' or 'active'",
    )
    limit: Optional[int] = Field(default=25, description="Max campaigns to return", ge=1, le=100)


class GetCampaignInput(BaseModel):
    """Input for getting a single campaign."""
    model_config = ConfigDict(extra="forbid")

    campaign_id: int = Field(..., description="Campaign ID to retrieve", ge=1)


class GetAccountSignalsInput(BaseModel):
    """Input for getting intent signals by company name."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    company_name: str = Field(
        ...,
        description="Company name to look up signals for (e.g., 'Shopify')",
        min_length=1,
        max_length=200,
    )


class GetContributorsInput(BaseModel):
    """Input for getting contributors/prospects for a company."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    company_name: str = Field(
        ...,
        description="Company name or GitHub org to look up contributors for",
        min_length=1,
        max_length=200,
    )
    has_email: Optional[bool] = Field(
        default=None,
        description="If True, only return contributors with email addresses",
    )
    limit: Optional[int] = Field(default=50, description="Max contributors to return", ge=1, le=500)


class GenerateEmailInput(BaseModel):
    """Input for generating a personalized cold email."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    company_name: str = Field(..., description="Target company name", min_length=1)
    first_name: str = Field(..., description="Prospect first name", min_length=1)
    last_name: str = Field(..., description="Prospect last name", min_length=1)
    title: str = Field(..., description="Prospect job title", min_length=1)
    email: str = Field(..., description="Prospect email address", min_length=3)
    campaign_id: Optional[int] = Field(
        default=None,
        description="Campaign ID to use for tone/prompt. If omitted, uses default settings.",
        ge=1,
    )
    variant: Optional[str] = Field(
        default=None,
        description="Email variant: 'A', 'B', or 'C'. If omitted, returns best-scored variant.",
    )


class GenerateEmailSequenceInput(BaseModel):
    """Input for generating a full multi-email outreach sequence."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    company_name: str = Field(..., description="Target company name", min_length=1)
    first_name: str = Field(..., description="Prospect first name", min_length=1)
    last_name: str = Field(..., description="Prospect last name", min_length=1)
    title: str = Field(..., description="Prospect job title", min_length=1)
    campaign_id: Optional[int] = Field(
        default=None,
        description="Campaign ID to use for tone/prompt",
        ge=1,
    )
    num_emails: Optional[int] = Field(
        default=4,
        description="Number of emails in the sequence (default 4)",
        ge=1,
        le=8,
    )


class GetCampaignProspectsInput(BaseModel):
    """Input for getting enrolled/discovered contacts for a campaign."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    campaign_id: int = Field(..., description="Campaign ID", ge=1)
    status: Optional[str] = Field(
        default=None,
        description="Filter by status: 'discovered', 'email_generated', 'enrolled', or 'failed'",
    )
    limit: Optional[int] = Field(default=100, description="Max contacts to return", ge=1, le=500)


class GetEnrollmentSummaryInput(BaseModel):
    """Input for getting enrollment pipeline status."""
    model_config = ConfigDict(extra="forbid")

    campaign_id: int = Field(..., description="Campaign ID", ge=1)


class CreateCampaignInput(BaseModel):
    """Input for creating a new campaign."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Campaign name", min_length=1, max_length=200)
    prompt: str = Field(
        ...,
        description="BDR instructions / campaign prompt (tone, key messages, context)",
        min_length=1,
    )
    assets: Optional[List[str]] = Field(
        default=None,
        description="List of asset URLs or descriptions (case studies, one-pagers, etc.)",
    )
    sequence_id: Optional[str] = Field(default=None, description="Apollo sequence ID to link")
    sequence_name: Optional[str] = Field(default=None, description="Apollo sequence name")
    tone: Optional[str] = Field(
        default="consultative",
        description="Email tone: 'consultative', 'direct', 'casual', etc.",
    )


class AddCampaignPersonaInput(BaseModel):
    """Input for adding a persona to a campaign."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    campaign_id: int = Field(..., description="Campaign ID to add persona to", ge=1)
    persona_name: str = Field(..., description="Persona name (e.g., 'Engineering Leader')", min_length=1)
    titles: List[str] = Field(
        ...,
        description="Job titles to target (e.g., ['VP Engineering', 'Director of Engineering'])",
        min_length=1,
    )
    seniorities: Optional[List[str]] = Field(
        default=None,
        description="Seniority levels (e.g., ['vp', 'director', 'c_suite'])",
    )
    sequence_id: Optional[str] = Field(default=None, description="Apollo sequence ID for this persona")
    sequence_name: Optional[str] = Field(default=None, description="Apollo sequence name for this persona")
    priority: Optional[int] = Field(default=0, description="Persona priority (lower = higher priority)", ge=0)


class EnrollContributorInput(BaseModel):
    """Input for enrolling a Dossier contributor into a campaign's Apollo sequence."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    contributor_id: int = Field(..., description="Dossier contributor ID", ge=1)
    campaign_id: int = Field(..., description="Campaign ID (determines which Apollo sequence)", ge=1)
    sender_email_account_id: Optional[str] = Field(
        default=None,
        description="Apollo email account ID to send from. If omitted, uses first active account.",
    )


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
        except Exception as e:
            logging.error(f"[MCP] Failed to save signals for report {report_id}: {e}")

    # Update account status
    try:
        update_account_status(scan_data, report_id)
    except Exception as e:
        logging.error(f"[MCP] Failed to update account status for report {report_id}: {e}")

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
# BDR Workflow Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="dossier_list_campaigns",
    annotations={
        "title": "List Campaigns",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_list_campaigns(params: ListCampaignsInput) -> str:
    """List all campaigns with status and persona count.

    Args:
        params: status (optional 'draft'|'active'), limit (default 25).

    Returns:
        str: JSON list of campaigns with id, name, status, persona_count,
             tone, sequence_name, created_at.
    """
    campaigns = get_all_campaigns()
    if params.status:
        campaigns = [c for c in campaigns if c.get("status") == params.status]
    campaigns = campaigns[: params.limit]
    return _json_response({
        "total": len(campaigns),
        "campaigns": [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "status": c.get("status"),
                "persona_count": c.get("persona_count", 0),
                "tone": c.get("tone"),
                "sequence_name": c.get("sequence_name"),
                "created_at": c.get("created_at"),
                "updated_at": c.get("updated_at"),
            }
            for c in campaigns
        ],
    })


@mcp.tool(
    name="dossier_get_campaign",
    annotations={
        "title": "Get Campaign Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_get_campaign(params: GetCampaignInput) -> str:
    """Get full campaign details including personas and settings.

    Args:
        params: campaign_id (int).

    Returns:
        str: JSON with campaign prompt, assets, personas (titles, seniorities,
             sequence), and all settings.
    """
    campaign = get_campaign(params.campaign_id)
    if not campaign:
        return _json_response({"error": f"Campaign {params.campaign_id} not found"})
    return _json_response(campaign)


@mcp.tool(
    name="dossier_get_account_signals",
    annotations={
        "title": "Get Account Intent Signals",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_get_account_signals(params: GetAccountSignalsInput) -> str:
    """Get intent signals for a company by name (no report_id needed).

    Searches for the company's latest report and returns its signals,
    intent score, and maturity level.

    Args:
        params: company_name (str).

    Returns:
        str: JSON with company_name, report_id, signals list, intent_score,
             maturity, last_scanned_at.
    """
    # Find latest report for this company
    reports = search_reports(params.company_name)
    if not reports:
        return _json_response({
            "error": f"No reports found for '{params.company_name}'. "
                     "Run dossier_scan_company first.",
        })

    latest = reports[0]
    report_id = latest.get("id")

    # Get signals
    signals = get_signals_for_report(report_id)

    # Get account for enrichment data
    account = get_account_by_company_case_insensitive(params.company_name)

    return _json_response({
        "company_name": latest.get("company_name", params.company_name),
        "report_id": report_id,
        "signals": signals or [],
        "signal_count": len(signals) if signals else 0,
        "intent_score": account.get("intent_score") if account else None,
        "maturity": account.get("maturity") if account else None,
        "tier": account.get("tier") if account else None,
        "last_scanned_at": account.get("last_scanned_at") if account else latest.get("created_at"),
    })


@mcp.tool(
    name="dossier_get_contributors",
    annotations={
        "title": "Get Company Contributors",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_get_contributors(params: GetContributorsInput) -> str:
    """Get contributors/prospects discovered for a company.

    Returns GitHub contributors with names, emails, titles, and contribution
    counts. Use has_email=True to filter to enrollable contacts only.

    Args:
        params: company_name (str), has_email (optional bool), limit (default 50).

    Returns:
        str: JSON list of contributors with name, github_login, email, title,
             company, contributions, insight, apollo_status.
    """
    contributors = get_contributors_by_company(params.company_name)

    if params.has_email:
        contributors = [c for c in contributors if c.get("email")]

    contributors = contributors[: params.limit]

    return _json_response({
        "company": params.company_name,
        "total": len(contributors),
        "contributors": [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "github_login": c.get("github_login"),
                "email": c.get("email"),
                "title": c.get("title"),
                "company": c.get("company"),
                "contributions": c.get("contributions"),
                "insight": c.get("insight"),
                "apollo_status": c.get("apollo_status"),
                "linkedin_url": c.get("linkedin_url"),
            }
            for c in contributors
        ],
    })


@mcp.tool(
    name="dossier_get_email_context",
    annotations={
        "title": "Get Email Generation Context",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_get_email_context(params: GenerateEmailInput) -> str:
    """Get all context needed to write a personalized cold email.

    Returns company signals, campaign prompt, account data, and prospect
    info. Use this data to craft the email in conversation.

    Args:
        params: company_name, first_name, last_name, title, email,
                campaign_id (optional), variant (optional).

    Returns:
        str: JSON with prospect info, signals, campaign prompt, account
             data, and persona classification.
    """
    # Get signals for the company
    reports = search_reports(params.company_name)
    signals = []
    if reports:
        signals = get_signals_for_report(reports[0].get("id")) or []

    # Get account enrichment data
    account = get_account_by_company_case_insensitive(params.company_name)

    # Get campaign prompt if specified
    campaign_prompt = ""
    campaign_data = None
    if params.campaign_id:
        campaign_data = get_campaign(params.campaign_id)
        if campaign_data:
            campaign_prompt = campaign_data.get("prompt", "")

    return _json_response({
        "prospect": {
            "first_name": params.first_name,
            "last_name": params.last_name,
            "title": params.title,
            "email": params.email,
            "company_name": params.company_name,
        },
        "signals": signals,
        "signal_count": len(signals),
        "account": {
            "intent_score": account.get("intent_score") if account else None,
            "maturity": account.get("maturity") if account else None,
            "tier": account.get("tier") if account else None,
            "website": account.get("website") if account else None,
            "annual_revenue": account.get("annual_revenue") if account else None,
        } if account else None,
        "campaign": {
            "id": campaign_data.get("id"),
            "name": campaign_data.get("name"),
            "prompt": campaign_prompt,
            "tone": campaign_data.get("tone"),
            "assets": campaign_data.get("assets", []),
        } if campaign_data else None,
    })


@mcp.tool(
    name="dossier_get_sequence_context",
    annotations={
        "title": "Get Sequence Generation Context",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_get_sequence_context(params: GenerateEmailSequenceInput) -> str:
    """Get all context needed to write a full multi-email outreach sequence.

    Returns company signals, campaign prompt, account data, and prospect
    info along with sequence structure guidance. Use this data to craft
    the email sequence in conversation.

    Args:
        params: company_name, first_name, last_name, title,
                campaign_id (optional), num_emails (default 4).

    Returns:
        str: JSON with prospect info, signals, campaign prompt, account
             data, and sequence structure guidance.
    """
    # Get signals for the company
    reports = search_reports(params.company_name)
    signals = []
    if reports:
        signals = get_signals_for_report(reports[0].get("id")) or []

    # Get account enrichment data
    account = get_account_by_company_case_insensitive(params.company_name)

    # Get campaign info if specified
    campaign_prompt = ""
    campaign_data = None
    if params.campaign_id:
        campaign_data = get_campaign(params.campaign_id)
        if campaign_data:
            campaign_prompt = campaign_data.get("prompt", "")

    return _json_response({
        "prospect": {
            "first_name": params.first_name,
            "last_name": params.last_name,
            "title": params.title,
            "company_name": params.company_name,
        },
        "num_emails": params.num_emails,
        "sequence_structure": {
            "1": "Hook + value proposition — reference a specific signal",
            "2": "Different angle — address a pain point from another signal or perspective",
            "3": "Lighter touch — social proof, case study, or quick insight",
            "4": "Breakup email — final value add, clear CTA, graceful close",
        },
        "signals": signals,
        "signal_count": len(signals),
        "account": {
            "intent_score": account.get("intent_score") if account else None,
            "maturity": account.get("maturity") if account else None,
            "tier": account.get("tier") if account else None,
            "website": account.get("website") if account else None,
            "annual_revenue": account.get("annual_revenue") if account else None,
        } if account else None,
        "campaign": {
            "id": campaign_data.get("id"),
            "name": campaign_data.get("name"),
            "prompt": campaign_prompt,
            "tone": campaign_data.get("tone"),
            "assets": campaign_data.get("assets", []),
        } if campaign_data else None,
    })


@mcp.tool(
    name="dossier_get_campaign_prospects",
    annotations={
        "title": "Get Campaign Prospects",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_get_campaign_prospects(params: GetCampaignProspectsInput) -> str:
    """Get discovered/enrolled contacts for a campaign's latest batch.

    Args:
        params: campaign_id (int), status (optional filter), limit (default 100).

    Returns:
        str: JSON with batch summary and contacts list including name, email,
             title, company, status, and generated emails.
    """
    batches = get_enrollment_batches_for_campaign(params.campaign_id)
    if not batches:
        return _json_response({
            "error": f"No enrollment batches found for campaign {params.campaign_id}",
        })

    latest_batch = batches[0]
    batch_id = latest_batch.get("id")

    contacts = get_enrollment_contacts(
        batch_id=batch_id,
        status=params.status,
        limit=params.limit,
    )

    # Parse generated_emails_json for each contact
    for c in contacts:
        if isinstance(c.get("generated_emails_json"), str):
            try:
                c["generated_emails"] = json.loads(c["generated_emails_json"])
            except (json.JSONDecodeError, TypeError):
                c["generated_emails"] = None
        else:
            c["generated_emails"] = c.get("generated_emails_json")

    return _json_response({
        "campaign_id": params.campaign_id,
        "batch": {
            "id": batch_id,
            "status": latest_batch.get("status"),
            "created_at": latest_batch.get("created_at"),
        },
        "total_contacts": len(contacts),
        "contacts": [
            {
                "id": c.get("id"),
                "first_name": c.get("first_name"),
                "last_name": c.get("last_name"),
                "email": c.get("email"),
                "title": c.get("title"),
                "company_name": c.get("company_name"),
                "status": c.get("status"),
                "persona_name": c.get("persona_name"),
                "sequence_name": c.get("sequence_name"),
                "generated_emails": c.get("generated_emails"),
                "apollo_contact_id": c.get("apollo_contact_id"),
                "enrolled_at": c.get("enrolled_at"),
            }
            for c in contacts
        ],
    })


@mcp.tool(
    name="dossier_get_enrollment_summary",
    annotations={
        "title": "Get Enrollment Summary",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def dossier_get_enrollment_summary(params: GetEnrollmentSummaryInput) -> str:
    """Get enrollment pipeline status for a campaign's latest batch.

    Shows counts by status: discovered, email_generated, enrolled, failed, skipped.

    Args:
        params: campaign_id (int).

    Returns:
        str: JSON with batch_id, status, and contact counts by pipeline stage.
    """
    batches = get_enrollment_batches_for_campaign(params.campaign_id)
    if not batches:
        return _json_response({
            "error": f"No enrollment batches found for campaign {params.campaign_id}",
        })

    latest_batch = batches[0]
    batch_id = latest_batch.get("id")
    summary = get_enrollment_batch_summary(batch_id)

    return _json_response({
        "campaign_id": params.campaign_id,
        "batch_id": batch_id,
        "batch_status": latest_batch.get("status"),
        "batch_created_at": latest_batch.get("created_at"),
        "total_contacts": summary.get("total", 0),
        "discovered": summary.get("discovered", 0),
        "email_generated": summary.get("email_generated", 0),
        "enrolled": summary.get("enrolled", 0),
        "failed": summary.get("failed", 0),
        "skipped": summary.get("skipped", 0),
    })


@mcp.tool(
    name="dossier_create_campaign",
    annotations={
        "title": "Create Campaign",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def dossier_create_campaign(params: CreateCampaignInput) -> str:
    """Create a new campaign for BDR outreach.

    Creates a campaign in draft status with the given prompt, tone, and
    optional Apollo sequence linkage.

    Args:
        params: name, prompt, assets (optional), sequence_id (optional),
                sequence_name (optional), tone (default 'consultative').

    Returns:
        str: JSON with campaign_id, name, status, created_at.
    """
    result = create_campaign(
        name=params.name,
        prompt=params.prompt,
        assets=params.assets or [],
        sequence_id=params.sequence_id,
        sequence_name=params.sequence_name,
        tone=params.tone,
    )
    return _json_response({
        "campaign_id": result.get("id"),
        "name": result.get("name"),
        "status": "draft",
    })


@mcp.tool(
    name="dossier_add_campaign_persona",
    annotations={
        "title": "Add Campaign Persona",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def dossier_add_campaign_persona(params: AddCampaignPersonaInput) -> str:
    """Add a target persona to an existing campaign.

    Defines who to target (job titles, seniorities) and which Apollo
    sequence to use for this persona.

    Args:
        params: campaign_id, persona_name, titles, seniorities (optional),
                sequence_id (optional), sequence_name (optional), priority.

    Returns:
        str: JSON with persona_id, campaign_id, persona_name, titles.
    """
    # Verify campaign exists
    campaign = get_campaign(params.campaign_id)
    if not campaign:
        return _json_response({"error": f"Campaign {params.campaign_id} not found"})

    # Use campaign's sequence as default if persona doesn't specify one
    seq_id = params.sequence_id or campaign.get("sequence_id")
    seq_name = params.sequence_name or campaign.get("sequence_name")

    result = create_campaign_persona(
        campaign_id=params.campaign_id,
        persona_name=params.persona_name,
        titles=params.titles,
        seniorities=params.seniorities or [],
        sequence_id=seq_id or "",
        sequence_name=seq_name,
    )
    return _json_response({
        "persona_id": result.get("id"),
        "campaign_id": result.get("campaign_id"),
        "persona_name": result.get("persona_name"),
        "titles": params.titles,
        "seniorities": params.seniorities or [],
        "sequence_id": seq_id,
    })


@mcp.tool(
    name="dossier_enroll_contributor",
    annotations={
        "title": "Enroll Contributor in Campaign Sequence",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dossier_enroll_contributor(params: EnrollContributorInput) -> str:
    """One-call enrollment: takes a Dossier contributor and enrolls them
    into the campaign's Apollo sequence.

    Replaces the manual 3-step chain of apollo_create_contact →
    apollo_search_sequences → apollo_enroll_contact.

    Steps:
    1. Fetch contributor from Dossier DB
    2. Fetch campaign to get linked sequence_id
    3. Create/find contact in Apollo
    4. Enroll into the campaign's sequence

    Args:
        params: contributor_id, campaign_id, sender_email_account_id (optional).

    Returns:
        str: JSON with enrolled status, contributor name, apollo_contact_id,
             sequence_id, or error details.
    """
    # Step 1: Get contributor
    contributor = get_contributor_by_id(params.contributor_id)
    if not contributor:
        return _json_response({
            "enrolled": False,
            "error": f"Contributor {params.contributor_id} not found",
            "failed_step": "fetch_contributor",
        })

    email = contributor.get("email")
    if not email:
        return _json_response({
            "enrolled": False,
            "error": f"Contributor {contributor.get('name', params.contributor_id)} has no email address",
            "failed_step": "validate_email",
        })

    # Step 2: Get campaign and sequence
    campaign = get_campaign(params.campaign_id)
    if not campaign:
        return _json_response({
            "enrolled": False,
            "error": f"Campaign {params.campaign_id} not found",
            "failed_step": "fetch_campaign",
        })

    sequence_id = campaign.get("sequence_id")
    if not sequence_id:
        return _json_response({
            "enrolled": False,
            "error": f"Campaign '{campaign.get('name')}' has no linked Apollo sequence",
            "failed_step": "get_sequence_id",
        })

    # Get Apollo headers
    try:
        headers = _apollo_headers()
    except ValueError as e:
        return _json_response({
            "enrolled": False,
            "error": str(e),
            "failed_step": "apollo_auth",
        })

    # Step 3: Create/find contact in Apollo
    first_name = contributor.get("name", "").split()[0] if contributor.get("name") else ""
    last_name = " ".join(contributor.get("name", "").split()[1:]) if contributor.get("name") else ""
    contact_payload = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
    }
    if contributor.get("company"):
        contact_payload["organization_name"] = contributor["company"]
    if contributor.get("title"):
        contact_payload["title"] = contributor["title"]

    try:
        resp = _requests.post(
            f"{_APOLLO_BASE}/v1/contacts",
            json=contact_payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code not in (200, 201):
            return _json_response({
                "enrolled": False,
                "error": f"Apollo create contact returned {resp.status_code}: {resp.text[:300]}",
                "failed_step": "create_apollo_contact",
            })
        apollo_contact_id = resp.json().get("contact", {}).get("id")
    except Exception as e:
        return _json_response({
            "enrolled": False,
            "error": f"Apollo create contact failed: {e}",
            "failed_step": "create_apollo_contact",
        })

    if not apollo_contact_id:
        return _json_response({
            "enrolled": False,
            "error": "Apollo returned no contact ID",
            "failed_step": "create_apollo_contact",
        })

    # Resolve sender email account
    sender_id = params.sender_email_account_id
    if not sender_id:
        try:
            acct_resp = _requests.get(f"{_APOLLO_BASE}/v1/email_accounts", headers=headers, timeout=10)
            if acct_resp.status_code == 200:
                accounts = acct_resp.json().get("email_accounts", [])
                active = [a for a in accounts if a.get("active")]
                if active:
                    sender_id = active[0]["id"]
        except Exception as e:
            logging.warning(f"[MCP] Failed to fetch Apollo email accounts: {e}")
    if not sender_id:
        return _json_response({
            "enrolled": False,
            "apollo_contact_id": apollo_contact_id,
            "error": "No sender email account found. Provide sender_email_account_id.",
            "failed_step": "resolve_sender",
        })

    # Step 4: Enroll into sequence
    enroll_payload = {
        "contact_ids": [apollo_contact_id],
        "emailer_campaign_id": sequence_id,
        "send_email_from_email_account_id": sender_id,
        "sequence_active_in_other_campaigns": False,
        "sequence_no_email": False,
    }

    try:
        resp = _requests.post(
            f"{_APOLLO_BASE}/api/v1/emailer_campaigns/{sequence_id}/add_contact_ids",
            json=enroll_payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code not in (200, 201):
            return _json_response({
                "enrolled": False,
                "apollo_contact_id": apollo_contact_id,
                "error": f"Apollo enrollment returned {resp.status_code}: {resp.text[:300]}",
                "failed_step": "enroll_in_sequence",
            })
    except Exception as e:
        return _json_response({
            "enrolled": False,
            "apollo_contact_id": apollo_contact_id,
            "error": f"Apollo enrollment failed: {e}",
            "failed_step": "enroll_in_sequence",
        })

    contributor_name = contributor.get("name", f"ID {params.contributor_id}")
    return _json_response({
        "enrolled": True,
        "contributor_name": contributor_name,
        "contributor_id": params.contributor_id,
        "apollo_contact_id": apollo_contact_id,
        "sequence_id": sequence_id,
        "sequence_name": campaign.get("sequence_name"),
        "campaign_name": campaign.get("name"),
        "sender_email_account_id": sender_id,
    })


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
        except Exception as e:
            logging.warning(f"[MCP] Failed to fetch Apollo email accounts for sender auto-detect: {e}")
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
        except Exception as e:
            logging.warning(f"[MCP] Failed to fetch Apollo email accounts for batch sender auto-detect: {e}")
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
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "5001"))
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
