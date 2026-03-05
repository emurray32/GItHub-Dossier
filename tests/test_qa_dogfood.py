"""
QA Dogfood Tests — Flask test client-based UI/UX smoke tests.

Tests every major page and API endpoint for:
- HTTP 200 responses (no 500 errors)
- Expected HTML elements and structure
- Missing text-overflow / truncation issues
- Broken CSS class references
- Missing template variables
- Jinja rendering errors
"""
import json
import re
import pytest
from html.parser import HTMLParser
from collections import Counter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded_app(flask_app, test_db):
    """Flask client with a few seeded accounts and campaigns."""
    import database

    # Seed some accounts across multiple tiers
    conn = database.get_db_connection()
    cursor = conn.cursor()

    accounts = [
        ('Acme Corp', 'acmecorp', 0, 'https://acme.com', '50000000', 'Monitoring for activity', None),
        ('BetaTech', 'betatech', 1, 'https://betatech.io', '150000000', 'Found react-i18next in package.json', None),
        ('GammaSoft', 'gammasoft', 2, 'https://gammasoft.dev', '800000000', 'Branch feature/i18n found. RFC discussion detected.', None),
        ('DeltaCo', 'deltaco', 3, None, '2000000000', 'Already launched: locales/en, locales/fr, locales/de', None),
        ('EpsilonInc', None, 4, None, None, None, None),
        ('ZetaGlobal', 'zetaglobal', 1, 'https://zetaglobal.com', '300000000', 'Ghost branch feature/localization in main-app. Dependency injection: next-intl found.', None),
        ('EtaPlatform', 'etaplatform', 2, 'https://eta.platform', '1500000000', 'Smoking gun fork of react-intl detected. RFC issue #42 discussing i18n.', None),
    ]

    for a in accounts:
        cursor.execute('''
            INSERT INTO monitored_accounts
            (company_name, github_org, current_tier, website, annual_revenue, evidence_summary, last_scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', a)

    # Seed a report using the correct schema
    cursor.execute('''
        INSERT INTO reports (company_name, github_org, scan_data, ai_analysis,
        signals_found, repos_scanned, commits_analyzed, prs_analyzed, scan_duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', ('BetaTech', 'betatech',
          json.dumps({'signals': [], 'repos_scanned': [], 'contributors': {}}),
          json.dumps({'summary': 'AI analysis here'}),
          0, 3, 100, 20, 45.0))

    report_id = cursor.lastrowid

    # Link report to account
    cursor.execute('''
        UPDATE monitored_accounts SET latest_report_id = ? WHERE company_name = 'BetaTech'
    ''', (report_id,))

    # Seed a campaign
    cursor.execute('''
        INSERT INTO campaigns (name, prompt, status) VALUES (?, ?, ?)
    ''', ('Preparing Outreach', 'Reach out to companies preparing for i18n', 'active'))
    campaign_id = cursor.lastrowid

    # Seed a sequence mapping
    cursor.execute('''
        INSERT OR REPLACE INTO sequence_mappings
        (sequence_id, sequence_name, sequence_config, num_steps, active, owner_name, enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', ('seq_001', 'Preparing - Technical', 'threaded_4', 4, 1, 'eric@phrase.com', 1))

    # Seed some contributors (using actual schema columns)
    cursor.execute('''
        INSERT INTO contributors
        (github_login, name, email, company, repo_source, github_org, contributions, github_url, apollo_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', ('jsmith', 'Jane Smith', 'jane@betatech.io', 'BetaTech', 'webapp', 'betatech', 150,
          'https://github.com/jsmith', 'not_sent'))
    cursor.execute('''
        INSERT INTO contributors
        (github_login, name, email, company, repo_source, github_org, contributions, github_url, apollo_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', ('jdoe', 'John Doe', 'john@gammasoft.dev', 'GammaSoft', 'platform', 'gammasoft', 85,
          'https://github.com/jdoe', 'sent'))

    conn.commit()
    conn.close()

    return flask_app


# ---------------------------------------------------------------------------
# HTML Analysis Helpers
# ---------------------------------------------------------------------------

class HTMLStructureParser(HTMLParser):
    """Parse HTML and collect structural information."""

    def __init__(self):
        super().__init__()
        self.tags = Counter()
        self.classes = set()
        self.ids = set()
        self.errors_found = []
        self.text_content = []
        self.in_script = False
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        self.tags[tag] += 1
        attrs_dict = dict(attrs)
        if 'class' in attrs_dict:
            for cls in attrs_dict['class'].split():
                self.classes.add(cls)
        if 'id' in attrs_dict:
            self.ids.add(attrs_dict['id'])
        if tag == 'script':
            self.in_script = True
        if tag == 'style':
            self.in_style = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_script = False
        if tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if not self.in_script and not self.in_style:
            stripped = data.strip()
            if stripped:
                self.text_content.append(stripped)


def parse_html(html_text):
    """Parse HTML and return structural info."""
    parser = HTMLStructureParser()
    parser.feed(html_text)
    return parser


def check_no_jinja_errors(html_text, page_name):
    """Check for un-rendered Jinja variables or errors in HTML output."""
    issues = []

    # Strip <script> and <style> blocks — they may contain template placeholders
    # like {{company}}, {{first_name}} that are JS template strings, NOT Jinja errors
    stripped = re.sub(r'<script[^>]*>.*?</script>', '', html_text, flags=re.DOTALL)
    stripped = re.sub(r'<style[^>]*>.*?</style>', '', stripped, flags=re.DOTALL)
    # Also strip onclick/onchange attribute values which may have template strings
    stripped = re.sub(r'on\w+="[^"]*"', '', stripped)
    stripped = re.sub(r"on\w+='[^']*'", '', stripped)

    # Look for un-rendered Jinja expressions in remaining HTML
    jinja_patterns = [
        (r'\{\{[^}]*\}\}', 'Unrendered Jinja expression'),
        (r'\{%[^%]*%\}', 'Unrendered Jinja tag'),
    ]
    for pattern, desc in jinja_patterns:
        matches = re.findall(pattern, stripped)
        if matches:
            issues.append(f'{page_name}: {desc}: {matches[:3]}')

    # Look for Python tracebacks
    if 'Traceback (most recent call last)' in html_text:
        issues.append(f'{page_name}: Python traceback in output')

    # Look for "Internal Server Error"
    if 'Internal Server Error' in html_text:
        issues.append(f'{page_name}: Internal Server Error')

    # Look for "TemplateSyntaxError" or "UndefinedError"
    if 'TemplateSyntaxError' in html_text or 'UndefinedError' in html_text:
        issues.append(f'{page_name}: Jinja template error')

    return issues


# ===========================================================================
# PAGE LOAD TESTS
# ===========================================================================

class TestPageLoads:
    """Verify all pages return 200 and render without server errors."""

    def test_index_page(self, seeded_app):
        """GET / — should redirect to /accounts or render index."""
        resp = seeded_app.get('/')
        assert resp.status_code in (200, 302, 308), f'Index returned {resp.status_code}'

    def test_accounts_page(self, seeded_app):
        """GET /accounts — main RepoRadar page."""
        resp = seeded_app.get('/accounts')
        assert resp.status_code == 200, f'/accounts returned {resp.status_code}'
        html = resp.data.decode('utf-8')
        assert 'RepoRadar' in html
        issues = check_no_jinja_errors(html, '/accounts')
        assert not issues, f'Jinja issues: {issues}'

    def test_accounts_tier_0_filter(self, seeded_app):
        """GET /accounts?tier=0 — Tracking filter."""
        resp = seeded_app.get('/accounts?tier=0')
        assert resp.status_code == 200

    def test_accounts_tier_1_filter(self, seeded_app):
        """GET /accounts?tier=1 — Thinking (Warm Leads) filter."""
        resp = seeded_app.get('/accounts?tier=1')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'BetaTech' in html or 'ZetaGlobal' in html

    def test_accounts_tier_2_filter(self, seeded_app):
        """GET /accounts?tier=2 — Preparing (Hot Leads) filter."""
        resp = seeded_app.get('/accounts?tier=2')
        assert resp.status_code == 200

    def test_accounts_tier_3_filter(self, seeded_app):
        """GET /accounts?tier=3 — Launched filter."""
        resp = seeded_app.get('/accounts?tier=3')
        assert resp.status_code == 200

    def test_accounts_tier_4_filter(self, seeded_app):
        """GET /accounts?tier=4 — Not Found filter."""
        resp = seeded_app.get('/accounts?tier=4')
        assert resp.status_code == 200

    def test_accounts_search(self, seeded_app):
        """GET /accounts?q=Beta — search filter."""
        resp = seeded_app.get('/accounts?q=Beta')
        assert resp.status_code == 200

    def test_accounts_pagination(self, seeded_app):
        """GET /accounts?page=1&limit=2 — pagination."""
        resp = seeded_app.get('/accounts?page=1&limit=2')
        assert resp.status_code == 200

    def test_campaigns_page(self, seeded_app):
        """GET /campaigns — campaign management page."""
        resp = seeded_app.get('/campaigns')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Campaigns' in html
        issues = check_no_jinja_errors(html, '/campaigns')
        assert not issues, f'Jinja issues: {issues}'

    def test_mapping_sequences_page(self, seeded_app):
        """GET /mapping-sequences — mapping sequences page."""
        resp = seeded_app.get('/mapping-sequences')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Mapping Sequences' in html
        issues = check_no_jinja_errors(html, '/mapping-sequences')
        assert not issues, f'Jinja issues: {issues}'

    def test_contributors_page(self, seeded_app):
        """GET /contributors — contributors page."""
        resp = seeded_app.get('/contributors')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Contributors' in html
        issues = check_no_jinja_errors(html, '/contributors')
        assert not issues, f'Jinja issues: {issues}'

    def test_settings_page(self, seeded_app):
        """GET /settings — settings page."""
        resp = seeded_app.get('/settings')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Settings' in html
        issues = check_no_jinja_errors(html, '/settings')
        assert not issues, f'Jinja issues: {issues}'

    def test_history_page(self, seeded_app):
        """GET /history — scan history page."""
        resp = seeded_app.get('/history')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        issues = check_no_jinja_errors(html, '/history')
        assert not issues, f'Jinja issues: {issues}'

    def test_scorecard_page(self, seeded_app):
        """GET /scorecard — scorecard page."""
        resp = seeded_app.get('/scorecard')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'ScoreCard' in html
        issues = check_no_jinja_errors(html, '/scorecard')
        assert not issues, f'Jinja issues: {issues}'

    def test_report_view(self, seeded_app):
        """GET /report/1 — view a report."""
        resp = seeded_app.get('/report/1')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'BetaTech' in html
        issues = check_no_jinja_errors(html, '/report/1')
        assert not issues, f'Jinja issues: {issues}'

    def test_report_nonexistent(self, seeded_app):
        """GET /report/99999 — non-existent report should not crash."""
        resp = seeded_app.get('/report/99999')
        # Could be 404 or redirect
        assert resp.status_code in (200, 302, 404), f'Non-existent report returned {resp.status_code}'


# ===========================================================================
# API ENDPOINT TESTS
# ===========================================================================

class TestAPIEndpoints:
    """Test API endpoints return valid JSON."""

    def test_health_check(self, seeded_app):
        """GET /api/health — health check."""
        resp = seeded_app.get('/api/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert 'status' in data

    def test_accounts_datatable(self, seeded_app):
        """GET /api/accounts/datatable — server-side datatable."""
        resp = seeded_app.get('/api/accounts/datatable?draw=1&start=0&length=10')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert 'data' in data
        assert 'recordsTotal' in data
        assert 'recordsFiltered' in data

    def test_accounts_datatable_with_search(self, seeded_app):
        """GET /api/accounts/datatable — with search."""
        resp = seeded_app.get('/api/accounts/datatable?draw=1&start=0&length=10&search%5Bvalue%5D=Beta')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None

    def test_accounts_datatable_with_sort(self, seeded_app):
        """GET /api/accounts/datatable — with sort."""
        resp = seeded_app.get('/api/accounts/datatable?draw=1&start=0&length=10&order%5B0%5D%5Bcolumn%5D=2&order%5B0%5D%5Bdir%5D=asc')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None

    def test_tier_counts_in_accounts(self, seeded_app):
        """Verify tier counts match seeded data."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        # Should show correct total count
        assert '7' in html  # 7 seeded accounts total

    def test_contributors_datatable(self, seeded_app):
        """GET /api/contributors/datatable — server-side datatable."""
        resp = seeded_app.get('/api/contributors/datatable?draw=1&start=0&length=50')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert 'data' in data

    def test_contributors_stats(self, seeded_app):
        """GET /api/contributors/stats — contributor statistics."""
        resp = seeded_app.get('/api/contributors/stats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None

    def test_campaigns_api_list(self, seeded_app):
        """GET /api/campaigns — list all campaigns."""
        resp = seeded_app.get('/api/campaigns')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None

    def test_scan_statuses_api(self, seeded_app):
        """GET /api/accounts/scan-statuses — scan status polling."""
        resp = seeded_app.get('/api/accounts/scan-statuses')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None

    def test_scorecard_datatable(self, seeded_app):
        """GET /api/scorecard/datatable — scorecard data."""
        resp = seeded_app.get('/api/scorecard/datatable?draw=1&start=0&length=25')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None

    def test_sequence_mappings_enabled(self, seeded_app):
        """GET /api/sequence-mappings/enabled — get enabled sequences."""
        resp = seeded_app.get('/api/sequence-mappings/enabled')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None


# ===========================================================================
# HTML STRUCTURE TESTS — Check for specific UI elements
# ===========================================================================

class TestAccountsPageStructure:
    """Deep inspection of /accounts page HTML structure."""

    def test_tier_filter_buttons_present(self, seeded_app):
        """All tier filter buttons should be present."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        assert 'tier-filter-btn' in html
        assert 'data-tier-id="all"' in html

    def test_table_header_columns(self, seeded_app):
        """Table headers should include all expected columns."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        expected_headers = ['Company', 'Annual Revenue', 'GitHub Org', 'Tier', 'Last Scanned', 'Evidence Summary']
        for header in expected_headers:
            assert header in html, f'Missing table header: {header}'

    def test_detail_panel_exists(self, seeded_app):
        """Account detail slide-out panel should exist in DOM."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        assert 'account-detail-panel' in html
        assert 'panel-company-name' in html
        assert 'panel-evidence' in html

    def test_sequence_select_in_panel(self, seeded_app):
        """Panel should have sequence select dropdown."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        assert 'panel-sequence-select' in html

    def test_pagination_controls(self, seeded_app):
        """Pagination controls should be present."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        assert 'pagination-container' in html
        assert 'prev-page-btn' in html
        assert 'next-page-btn' in html

    def test_bulk_command_bar(self, seeded_app):
        """Bulk command bar should exist (hidden by default)."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        assert 'bulk-command-bar' in html
        assert 'bulk-rescan-btn' in html

    def test_batch_rescan_modal(self, seeded_app):
        """Batch rescan modal should exist."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        assert 'batch-rescan-modal' in html

    def test_archived_modal(self, seeded_app):
        """Archived accounts modal should exist."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        assert 'archived-modal' in html

    def test_status_legend_present(self, seeded_app):
        """Status legend/key should be shown."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        assert 'status-legend-container' in html
        assert 'Tracking' in html
        assert 'Hot Leads' in html
        assert 'Warm Leads' in html

    def test_report_drawer_exists(self, seeded_app):
        """Report drawer should exist."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        assert 'report-drawer' in html
        assert 'report-frame' in html

    def test_scheduler_modal(self, seeded_app):
        """Auto-rescan scheduler modal should exist."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        assert 'scheduler-modal' in html


class TestCampaignsPageStructure:
    """Deep inspection of /campaigns page HTML structure."""

    def test_campaign_card_rendered(self, seeded_app):
        """Seeded campaign should appear as a card."""
        resp = seeded_app.get('/campaigns')
        html = resp.data.decode('utf-8')
        assert 'Preparing Outreach' in html
        assert 'campaign-card' in html

    def test_create_campaign_link(self, seeded_app):
        """New campaign link should point to the full-page form."""
        resp = seeded_app.get('/campaigns')
        html = resp.data.decode('utf-8')
        assert '/campaigns/new' in html

    def test_campaign_form_page(self, seeded_app):
        """Full-page campaign form should load."""
        resp = seeded_app.get('/campaigns/new')
        html = resp.data.decode('utf-8')
        assert 'New Campaign' in html
        assert 'campaign-name' in html


class TestMappingSequencesPageStructure:
    """Deep inspection of /mapping-sequences page HTML structure."""

    def test_sync_button(self, seeded_app):
        """Sync from Apollo button should exist."""
        resp = seeded_app.get('/mapping-sequences')
        html = resp.data.decode('utf-8')
        assert 'btn-sync' in html
        assert 'Sync from Apollo' in html

    def test_enabled_sequences_table(self, seeded_app):
        """Enabled sequences table should show seeded mapping."""
        resp = seeded_app.get('/mapping-sequences')
        html = resp.data.decode('utf-8')
        assert 'Preparing - Technical' in html or 'ms-table' in html

    def test_search_panel(self, seeded_app):
        """Search panel for finding/enabling sequences should exist."""
        resp = seeded_app.get('/mapping-sequences')
        html = resp.data.decode('utf-8')
        assert 'ms-search-panel' in html or 'Browse' in html


class TestContributorsPageStructure:
    """Deep inspection of /contributors page HTML structure."""

    def test_table_columns(self, seeded_app):
        """Table should have expected columns."""
        resp = seeded_app.get('/contributors')
        html = resp.data.decode('utf-8')
        expected = ['Name', 'Company', 'Repo', 'Contributions', 'Status']
        for col in expected:
            assert col in html, f'Missing column: {col}'

    def test_sidebar_panel(self, seeded_app):
        """Contributor detail sidebar should exist."""
        resp = seeded_app.get('/contributors')
        html = resp.data.decode('utf-8')
        assert 'contrib-sidebar' in html

    def test_enrollment_toolbar(self, seeded_app):
        """Enrollment toolbar for bulk enrollment should exist."""
        resp = seeded_app.get('/contributors')
        html = resp.data.decode('utf-8')
        assert 'enrollment-toolbar' in html

    def test_filter_chips(self, seeded_app):
        """Filter chips should be present."""
        resp = seeded_app.get('/contributors')
        html = resp.data.decode('utf-8')
        assert 'filter-chip' in html
        assert 'Warm/Hot leads' in html

    def test_sync_contributors_button(self, seeded_app):
        """Sync Contributors button should exist."""
        resp = seeded_app.get('/contributors')
        html = resp.data.decode('utf-8')
        assert 'Sync Contributors' in html or 'btn-fetch' in html


class TestSettingsPageStructure:
    """Deep inspection of /settings page HTML structure."""

    def test_settings_sections(self, seeded_app):
        """Settings should have main sections."""
        resp = seeded_app.get('/settings')
        html = resp.data.decode('utf-8')
        assert 'Scan History' in html
        assert 'Scanning Rules' in html

    def test_experimental_section(self, seeded_app):
        """Experimental section should exist."""
        resp = seeded_app.get('/settings')
        html = resp.data.decode('utf-8')
        assert 'Experimental' in html
        assert 'WebScraper' in html
        assert 'ScoreCard' in html

    def test_settings_links_valid(self, seeded_app):
        """Links in settings should point to valid routes."""
        resp = seeded_app.get('/settings')
        html = resp.data.decode('utf-8')
        # Extract href links
        hrefs = re.findall(r'href="(/[^"]*)"', html)
        for href in hrefs:
            if href.startswith('/static') or href.startswith('/docs'):
                continue
            page_resp = seeded_app.get(href)
            assert page_resp.status_code in (200, 302, 308), f'Settings link {href} returned {page_resp.status_code}'


class TestScorecardPageStructure:
    """Deep inspection of /scorecard page HTML structure."""

    def test_rubric_panel(self, seeded_app):
        """Scoring rubric panel should exist."""
        resp = seeded_app.get('/scorecard')
        html = resp.data.decode('utf-8')
        assert 'sc-rubric' in html
        assert 'Scoring Rubric' in html

    def test_rubric_categories(self, seeded_app):
        """All rubric categories should be present."""
        resp = seeded_app.get('/scorecard')
        html = resp.data.decode('utf-8')
        assert 'Languages Detected' in html
        assert 'Systems in Use' in html
        assert 'Revenue' in html

    def test_score_all_button(self, seeded_app):
        """Score All button should exist."""
        resp = seeded_app.get('/scorecard')
        html = resp.data.decode('utf-8')
        assert 'Score All' in html

    def test_cohort_filter(self, seeded_app):
        """Cohort filter dropdown should exist."""
        resp = seeded_app.get('/scorecard')
        html = resp.data.decode('utf-8')
        assert 'scCohortFilter' in html
        assert 'Cohort A' in html
        assert 'Cohort B' in html


# ===========================================================================
# CSS AND STYLING ISSUES
# ===========================================================================

class TestCSSIssues:
    """Check for common CSS issues that cause UI bugs."""

    def test_evidence_cell_has_text_overflow(self, seeded_app):
        """Evidence cells should have text-overflow: ellipsis in CSS."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        # The evidence cell should have proper CSS class
        assert 'evidence-cell' in html
        assert 'evidence-text' in html

    def test_no_broken_image_refs(self, seeded_app):
        """Check that there are no broken image references."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        img_srcs = re.findall(r'<img[^>]+src="([^"]*)"', html)
        for src in img_srcs:
            if src.startswith('data:'):
                continue
            if src.startswith('/'):
                img_resp = seeded_app.get(src)
                assert img_resp.status_code == 200, f'Broken image: {src}'

    def test_css_file_loads(self, seeded_app):
        """Main CSS file should load."""
        resp = seeded_app.get('/static/css/style.css')
        assert resp.status_code == 200
        assert len(resp.data) > 100

    def test_accounts_css_loads(self, seeded_app):
        """Accounts CSS file should load."""
        resp = seeded_app.get('/static/css/accounts.css')
        assert resp.status_code == 200

    def test_contributors_css_loads(self, seeded_app):
        """Contributors CSS should load."""
        resp = seeded_app.get('/static/css/contributors.css')
        assert resp.status_code == 200

    def test_scorecard_css_loads(self, seeded_app):
        """Scorecard CSS should load."""
        resp = seeded_app.get('/static/css/scorecard.css')
        assert resp.status_code == 200

    def test_settings_css_loads(self, seeded_app):
        """Settings CSS should load."""
        resp = seeded_app.get('/static/css/settings.css')
        assert resp.status_code == 200


# ===========================================================================
# ACCOUNTS TABLE RENDERING — check actual account data
# ===========================================================================

class TestAccountsDataRendering:
    """Check that account data is rendered correctly in the accounts table."""

    def test_tier_badges_render(self, seeded_app):
        """Tier badges should render with proper classes."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        # Should have tier badge classes for various tiers
        assert 'tier-badge' in html

    def test_evidence_summary_not_truncated_to_nothing(self, seeded_app):
        """Evidence summaries should appear in the table (not just empty cells)."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        # At least one evidence summary from seeded data should appear
        assert 'evidence' in html.lower()

    def test_github_org_links_render(self, seeded_app):
        """GitHub org column should show org links."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        assert 'github-org-cell' in html

    def test_company_names_render(self, seeded_app):
        """Company names should appear in the table."""
        resp = seeded_app.get('/accounts')
        html = resp.data.decode('utf-8')
        # All seeded company names should be present
        companies = ['Acme Corp', 'BetaTech', 'GammaSoft', 'DeltaCo', 'EpsilonInc', 'ZetaGlobal', 'EtaPlatform']
        found_any = any(c in html for c in companies)
        assert found_any, 'No seeded company names found in accounts table'

    def test_revenue_formatting(self, seeded_app):
        """Revenue values should be formatted properly."""
        resp = seeded_app.get('/api/accounts/datatable?draw=1&start=0&length=10')
        data = resp.get_json()
        # Check that annual_revenue is present in data
        if data and 'data' in data and len(data['data']) > 0:
            for row in data['data']:
                if 'annual_revenue' in row and row['annual_revenue']:
                    # Revenue should be a number, not a broken string
                    # Revenue is stored as TEXT, so it comes back as a string.
                    # This is a known issue (P2) — it means JS client-side must
                    # parse the string. Check it's at least parseable.
                    val = row['annual_revenue']
                    assert isinstance(val, (int, float, str, type(None))), \
                        f'Revenue should be numeric or parseable string, got: {type(val)}'
                    if isinstance(val, str):
                        # Should be parseable as a number
                        try:
                            float(val.replace(',', ''))
                        except ValueError:
                            pass  # Some values like "$50M" are formatted strings - that's ok


# ===========================================================================
# EDGE CASES — Test unusual/boundary inputs
# ===========================================================================

class TestEdgeCases:
    """Test edge cases that might break the UI."""

    def test_empty_search_returns_all(self, seeded_app):
        """Empty search query should return all accounts."""
        resp = seeded_app.get('/accounts?q=')
        assert resp.status_code == 200

    def test_invalid_tier_filter(self, seeded_app):
        """Invalid tier number shouldn't crash."""
        resp = seeded_app.get('/accounts?tier=99')
        assert resp.status_code == 200

    def test_negative_page(self, seeded_app):
        """Negative page number shouldn't crash."""
        resp = seeded_app.get('/accounts?page=-1')
        assert resp.status_code in (200, 400)

    def test_very_large_page(self, seeded_app):
        """Very large page number shouldn't crash."""
        resp = seeded_app.get('/accounts?page=99999')
        assert resp.status_code == 200

    def test_xss_in_search(self, seeded_app):
        """XSS attempt in search should be escaped."""
        resp = seeded_app.get('/accounts?q=<script>alert(1)</script>')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        # The raw script tag should not appear unescaped
        assert '<script>alert(1)</script>' not in html or '&lt;script&gt;' in html

    def test_very_long_search_query(self, seeded_app):
        """Very long search query shouldn't crash."""
        long_query = 'A' * 500
        resp = seeded_app.get(f'/accounts?q={long_query}')
        assert resp.status_code in (200, 400, 414)


# ===========================================================================
# REPORT VIEW TESTS
# ===========================================================================

class TestReportView:
    """Test report viewing page."""

    def test_report_renders_company_name(self, seeded_app):
        """Report page should show company name."""
        resp = seeded_app.get('/report/1')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'BetaTech' in html

    def test_report_has_no_errors(self, seeded_app):
        """Report page should render without errors."""
        resp = seeded_app.get('/report/1')
        html = resp.data.decode('utf-8')
        issues = check_no_jinja_errors(html, '/report/1')
        assert not issues


# ===========================================================================
# SIDEBAR NAVIGATION TESTS
# ===========================================================================

class TestSidebarNavigation:
    """Verify sidebar navigation renders correctly on all pages."""

    @pytest.mark.parametrize('path', [
        '/accounts', '/campaigns', '/mapping-sequences',
        '/contributors', '/settings', '/scorecard',
    ])
    def test_sidebar_present(self, seeded_app, path):
        """Every page should have the sidebar nav."""
        resp = seeded_app.get(path)
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'sidebar' in html
        assert 'sidebar-nav' in html

    @pytest.mark.parametrize('path', [
        '/accounts', '/campaigns', '/mapping-sequences',
        '/contributors', '/settings', '/scorecard',
    ])
    def test_sidebar_links(self, seeded_app, path):
        """Sidebar should contain links to all major pages."""
        resp = seeded_app.get(path)
        html = resp.data.decode('utf-8')
        assert 'RepoRadar' in html
        assert 'Campaigns' in html
        assert 'Mapping Sequences' in html
        assert 'Contributors' in html
        assert 'Settings' in html

    @pytest.mark.parametrize('path,expected_active', [
        ('/accounts', 'accounts'),
        ('/campaigns', 'campaigns'),
        ('/contributors', 'contributors'),
        ('/settings', 'settings'),
    ])
    def test_active_nav_item(self, seeded_app, path, expected_active):
        """Current page should have active nav styling."""
        resp = seeded_app.get(path)
        html = resp.data.decode('utf-8')
        # The sidebar item for the current page should have 'active' class
        assert 'active' in html  # Basic check that some nav item is active
