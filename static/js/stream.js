/**
 * Stream Handler for Lead Machine
 *
 * Handles Server-Sent Events for real-time scan progress updates.
 */

class StreamHandler {
    constructor(company) {
        this.company = company;
        this.consoleOutput = document.getElementById('consoleOutput');
        this.statusIndicator = document.getElementById('statusIndicator');
        this.statusText = document.getElementById('statusText');
        this.progressBar = document.getElementById('progressBar');
        this.actionButtons = document.getElementById('actionButtons');
        this.viewReportBtn = document.getElementById('viewReportBtn');
        this.reportPreview = document.getElementById('reportPreview');
        this.liveFindings = document.getElementById('liveFindings');
        this.findingsList = document.getElementById('findingsList');

        this.scanData = null;
        this.analysisData = null;
        this.reportId = null;
        this.lineCount = 0;
        this.phases = ['Discovery', 'Repository Discovery', 'Deep Repository Scan', 'Scan Complete'];
        this.currentPhase = 0;
        this.signalsFound = [];
    }

    start() {
        // Clear initial message
        if (this.consoleOutput) this.consoleOutput.innerHTML = '';
        this.updateStatus('Connecting...', 'active');

        const eventSource = new EventSource(`/stream_scan/${encodeURIComponent(this.company)}`);

        eventSource.onmessage = (event) => {
            this.handleMessage(event.data);
        };

        eventSource.onerror = (error) => {
            console.error('EventSource error:', error);
            this.updateStatus('Connection lost', 'error');
            this.addLine('Connection error. Please refresh to retry.', 'error');
            eventSource.close();
        };

        // Store reference for cleanup
        this.eventSource = eventSource;
    }

    handleMessage(data) {
        // Parse the message type and content
        const colonIndex = data.indexOf(':');
        if (colonIndex === -1) return;

        const type = data.substring(0, colonIndex);
        const content = data.substring(colonIndex + 1);

        switch (type) {
            case 'LOG':
                this.handleLogMessage(content);
                break;

            case 'ERROR':
                this.handleErrorMessage(content);
                break;

            case 'SIGNAL_FOUND':
                this.handleSignalFound(content);
                break;

            case 'SCAN_COMPLETE':
                this.handleScanComplete(content);
                break;

            case 'ANALYSIS_COMPLETE':
                this.handleAnalysisComplete(content);
                break;

            case 'COMPLETE':
                this.handleComplete(content);
                break;
        }
    }

    handleSignalFound(jsonStr) {
        try {
            const signal = JSON.parse(jsonStr);
            this.signalsFound.push(signal);

            // Show live findings panel if hidden
            if (this.liveFindings && this.liveFindings.classList.contains('hidden')) {
                this.liveFindings.classList.remove('hidden');
            }

            // Add to live findings list
            if (this.findingsList) {
                // Clear empty state message on first signal
                const emptyMsg = this.findingsList.querySelector('.findings-empty');
                if (emptyMsg) {
                    emptyMsg.remove();
                }

                const item = document.createElement('div');
                item.className = `finding-item finding-${signal.significance || 'medium'}`;

                // Format based on signal type
                let iconClass = 'info';
                let description = '';

                switch (signal.type) {
                    case 'competitor_config':
                        iconClass = 'warning';
                        description = `Competitor TMS: ${signal.file}`;
                        break;
                    case 'frustration':
                        iconClass = 'danger';
                        description = `Pain Point: "${signal.message}..."`;
                        break;
                    case 'new_locale_file':
                        iconClass = 'success';
                        description = `New locale: ${signal.file}`;
                        break;
                    case 'locale_inventory':
                        iconClass = 'info';
                        description = `${signal.count} locales in ${signal.repo}`;
                        break;
                    case 'seo_i18n_config':
                        iconClass = 'success';
                        description = `SEO i18n: ${signal.source} (${signal.locales?.length || 0} locales)`;
                        break;
                    case 'greenfield_opportunity':
                        iconClass = 'success';
                        description = `Greenfield! ${signal.total_stars}+ stars, no i18n`;
                        break;
                    case 'i18n_pr':
                        iconClass = 'info';
                        description = `PR #${signal.pr_number}: ${signal.title}`;
                        break;
                    default:
                        description = `${signal.type}: ${signal.repo || ''}`;
                }

                item.innerHTML = `
                    <span class="finding-icon ${iconClass}" aria-hidden="true"></span>
                    <span class="finding-text">${this.escapeHtml(description)}</span>
                    ${signal.repo ? `<span class="finding-repo">${this.escapeHtml(signal.repo)}</span>` : ''}
                `;

                this.findingsList.appendChild(item);

                // Auto-scroll to latest finding
                this.findingsList.scrollTop = this.findingsList.scrollHeight;
            }

            // Update status with signal count
            this.updateStatus(`Found ${this.signalsFound.length} signals...`, 'active');

        } catch (e) {
            console.error('Error parsing signal:', e);
        }
    }

    handleLogMessage(message) {
        // Detect phase changes
        if (message.includes('PHASE')) {
            this.currentPhase++;
            this.updateProgress();
            this.addLine(message, 'phase');
        } else if (message.startsWith('-') || message.startsWith('=')) {
            this.addLine(message, 'separator');
        } else if (message.includes('Found') || message.includes('complete')) {
            this.addLine(message, 'success');
        } else {
            this.addLine(message);
        }

        this.updateStatus('Scanning...', 'active');
    }

    handleErrorMessage(message) {
        this.addLine(message, 'error');
        this.updateStatus('Error occurred', 'error');
    }

    handleScanComplete(jsonStr) {
        try {
            this.scanData = JSON.parse(jsonStr);
            this.addLine('', '');
            this.addLine('Scan complete! Generating AI analysis...', 'success');
            this.updateProgress(80);
        } catch (e) {
            console.error('Error parsing scan data:', e);
        }
    }

    handleAnalysisComplete(jsonStr) {
        try {
            this.analysisData = JSON.parse(jsonStr);
            this.addLine('AI analysis complete!', 'success');
            this.updateProgress(95);
        } catch (e) {
            console.error('Error parsing analysis data:', e);
        }
    }

    handleComplete(jsonStr) {
        try {
            const result = JSON.parse(jsonStr);
            this.reportId = result.report_id;
            this.scanData = result.scan_data;
            this.analysisData = result.analysis;

            // Close the event source
            if (this.eventSource) {
                this.eventSource.close();
            }

            this.updateProgress(100);
            this.updateStatus('Complete', 'complete');

            this.addLine('', '');
            this.addLine('=' .repeat(50), 'separator');
            this.addLine('SCAN COMPLETE', 'phase');
            this.addLine(`Duration: ${result.duration_seconds.toFixed(1)} seconds`, 'success');

            if (this.reportId) {
                this.addLine(`Report saved with ID: ${this.reportId}`, 'success');
            }

            // Show action buttons
            this.showActionButtons();

            // Show report preview
            this.showReportPreview();

        } catch (e) {
            console.error('Error parsing complete data:', e);
            this.updateStatus('Error', 'error');
        }
    }

    addLine(text, className = '') {
        const line = document.createElement('div');
        line.className = 'console-line';
        if (className) {
            line.classList.add(className);
        }
        line.textContent = text;
        if (!this.consoleOutput) return;
        this.consoleOutput.appendChild(line);

        // Auto-scroll to bottom
        this.consoleOutput.scrollTop = this.consoleOutput.scrollHeight;

        this.lineCount++;
    }

    updateStatus(text, state) {
        if (this.statusText) this.statusText.textContent = text;
        if (this.statusIndicator) {
            this.statusIndicator.className = 'status-indicator';
            if (state === 'complete') {
                this.statusIndicator.classList.add('complete');
            } else if (state === 'error') {
                this.statusIndicator.classList.add('error');
            }
        }
    }

    updateProgress(percent = null) {
        if (percent !== null) {
            this.progressBar.style.width = `${percent}%`;
        } else {
            // Calculate based on phase
            const phasePercent = (this.currentPhase / this.phases.length) * 70;
            this.progressBar.style.width = `${phasePercent}%`;
        }
    }

    showActionButtons() {
        this.actionButtons.classList.remove('hidden');

        if (this.reportId) {
            this.viewReportBtn.href = `/report/${this.reportId}`;
        } else {
            // If no report ID, show inline report
            this.viewReportBtn.style.display = 'none';
        }
    }

    showReportPreview() {
        if (!this.analysisData) return;

        const preview = document.createElement('div');
        preview.className = 'report-preview-content';

        // Executive Summary
        if (this.analysisData.executive_summary) {
            preview.innerHTML += `
                <div class="preview-section">
                    <h3>Executive Summary</h3>
                    <div class="preview-summary">${this.escapeHtml(this.analysisData.executive_summary)}</div>
                </div>
            `;
        }

        // Quick Stats
        if (this.scanData) {
            const statsSection = document.createElement('div');
            statsSection.className = 'preview-section';

            const statsHeading = document.createElement('h3');
            statsHeading.textContent = 'Quick Stats';
            statsSection.appendChild(statsHeading);

            const statsContainer = document.createElement('div');
            statsContainer.className = 'preview-stats';

            const statsConfig = [
                { value: this.scanData.signals?.length || 0, label: 'signals' },
                { value: this.scanData.repos_scanned?.length || 0, label: 'repos' },
                { value: this.scanData.total_commits_analyzed || 0, label: 'commits' },
                { value: this.scanData.total_prs_analyzed || 0, label: 'PRs' },
            ];

            statsConfig.forEach(({ value, label }) => {
                const stat = document.createElement('span');
                stat.className = 'stat';
                const strong = document.createElement('strong');
                strong.textContent = value;
                stat.appendChild(strong);
                stat.appendChild(document.createTextNode(' ' + label));
                statsContainer.appendChild(stat);
            });

            statsSection.appendChild(statsContainer);
            preview.appendChild(statsSection);
        }

        // Maturity & Score
        if (this.analysisData.localization_maturity) {
            const allowedMaturities = ['emerging', 'developing', 'mature', 'advanced'];
            const rawMaturity = this.analysisData.localization_maturity;
            const maturityClass = allowedMaturities.includes(rawMaturity) ? rawMaturity : 'unknown';
            const maturityText = this.escapeHtml(rawMaturity);
            const score = this.escapeHtml(String(this.analysisData.opportunity_score || 5));
            preview.innerHTML += `
                <div class="preview-section">
                    <h3>Assessment</h3>
                    <div style="display: flex; gap: 1rem; align-items: center;">
                        <span class="maturity-badge maturity-${maturityClass}">${maturityText}</span>
                        <span style="font-weight: 600;">Opportunity Score: ${score}/10</span>
                    </div>
                </div>
            `;
        }

        // Email Draft Preview
        if (this.analysisData.email_draft) {
            preview.innerHTML += `
                <div class="preview-section" style="background: var(--bg-primary); border: 1px solid var(--border-color); padding: 1rem; border-radius: 8px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                        <h3 style="margin: 0;">Email Draft Generated</h3>
                        <span class="badge badge-primary">Skill: Cold Outreach</span>
                    </div>
                    <div style="font-size: 0.875rem; color: var(--text-secondary); margin-bottom: 0.5rem;">
                        <strong>Subject:</strong> ${this.escapeHtml(this.analysisData.email_draft.subject)}
                    </div>
                    <div style="font-size: 0.875rem; white-space: pre-line; line-height: 1.5; color: var(--text-primary); max-height: 150px; overflow-y: auto; padding: 0.75rem; background: var(--bg-secondary); border-radius: 4px;">
                        ${this.escapeHtml(this.analysisData.email_draft.body)}
                    </div>
                </div>
            `;
        }

        this.reportPreview.innerHTML = '';
        this.reportPreview.appendChild(preview);
        this.reportPreview.classList.remove('hidden');

        // Add preview styles
        this.addPreviewStyles();
    }

    addPreviewStyles() {
        if (document.getElementById('preview-styles')) return;

        const styles = document.createElement('style');
        styles.id = 'preview-styles';
        styles.textContent = `
            .report-preview-content {
                background: var(--color-surface);
                border: 1px solid var(--color-border);
                border-radius: 8px;
                padding: 16px;
                margin-top: 1.5rem;
            }

            .preview-section {
                margin-bottom: 16px;
            }

            .preview-section:last-child {
                margin-bottom: 0;
            }

            .preview-section h3 {
                font-size: 14px;
                font-weight: 600;
                color: var(--color-primary);
                margin-bottom: 6px;
            }

            .preview-summary {
                font-size: 14px;
                line-height: 1.6;
                color: var(--color-text);
            }

            .preview-stats {
                display: flex;
                gap: 1.5rem;
                flex-wrap: wrap;
            }

            .preview-stats .stat {
                font-size: 13px;
                color: var(--color-text-secondary);
            }

            .preview-stats .stat strong {
                font-size: 20px;
                color: var(--color-primary);
            }

            .maturity-badge {
                display: inline-block;
                padding: 2px 10px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 600;
                text-transform: uppercase;
            }

            .maturity-emerging { background: #fef3c7; color: #92400e; }
            .maturity-developing { background: #dbeafe; color: #1e40af; }
            .maturity-mature { background: #d1fae5; color: #065f46; }
            .maturity-advanced { background: #ede9fe; color: #5b21b6; }
        `;
        document.head.appendChild(styles);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Export for use in templates
window.StreamHandler = StreamHandler;
