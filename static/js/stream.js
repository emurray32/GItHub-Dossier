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

        this.scanData = null;
        this.analysisData = null;
        this.reportId = null;
        this.lineCount = 0;
        this.phases = ['Discovery', 'Repository Discovery', 'Deep Repository Scan', 'Scan Complete'];
        this.currentPhase = 0;
    }

    start() {
        // Clear initial message
        this.consoleOutput.innerHTML = '';
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
        this.consoleOutput.appendChild(line);

        // Auto-scroll to bottom
        this.consoleOutput.scrollTop = this.consoleOutput.scrollHeight;

        this.lineCount++;
    }

    updateStatus(text, state) {
        this.statusText.textContent = text;
        this.statusIndicator.className = 'status-indicator';
        if (state === 'complete') {
            this.statusIndicator.classList.add('complete');
        } else if (state === 'error') {
            this.statusIndicator.classList.add('error');
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
            preview.innerHTML += `
                <div class="preview-section">
                    <h3>Quick Stats</h3>
                    <div class="preview-stats">
                        <span class="stat">
                            <strong>${this.scanData.signals?.length || 0}</strong> signals
                        </span>
                        <span class="stat">
                            <strong>${this.scanData.repos_scanned?.length || 0}</strong> repos
                        </span>
                        <span class="stat">
                            <strong>${this.scanData.total_commits_analyzed || 0}</strong> commits
                        </span>
                        <span class="stat">
                            <strong>${this.scanData.total_prs_analyzed || 0}</strong> PRs
                        </span>
                    </div>
                </div>
            `;
        }

        // Maturity & Score
        if (this.analysisData.localization_maturity) {
            const maturity = this.analysisData.localization_maturity;
            const score = this.analysisData.opportunity_score || 5;
            preview.innerHTML += `
                <div class="preview-section">
                    <h3>Assessment</h3>
                    <p>
                        <strong>Maturity:</strong>
                        <span class="maturity-badge maturity-${maturity}">${maturity}</span>
                    </p>
                    <p>
                        <strong>Opportunity Score:</strong> ${score}/10
                    </p>
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
                background: var(--bg-secondary);
                border: 1px solid var(--border-color);
                border-radius: 8px;
                padding: 1.5rem;
                margin-top: 2rem;
            }

            .preview-section {
                margin-bottom: 1.5rem;
            }

            .preview-section:last-child {
                margin-bottom: 0;
            }

            .preview-section h3 {
                font-size: 1rem;
                color: var(--accent-color);
                margin-bottom: 0.5rem;
            }

            .preview-summary {
                font-size: 1.125rem;
                line-height: 1.6;
                color: var(--text-primary);
            }

            .preview-stats {
                display: flex;
                gap: 2rem;
                flex-wrap: wrap;
            }

            .preview-stats .stat {
                font-size: 0.875rem;
                color: var(--text-secondary);
            }

            .preview-stats .stat strong {
                font-size: 1.25rem;
                color: var(--accent-color);
            }

            .maturity-badge {
                display: inline-block;
                padding: 0.25rem 0.75rem;
                border-radius: 20px;
                font-size: 0.875rem;
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
