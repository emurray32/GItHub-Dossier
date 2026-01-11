/* global Chart */

document.addEventListener('DOMContentLoaded', () => {
    const webhookForm = document.getElementById('webhookForm');
    const webhookUrlInput = document.getElementById('webhookUrl');
    const webhookStatus = document.getElementById('webhookStatus');
    const testButton = document.getElementById('testWebhookBtn');

    const setStatus = (message, tone = 'info') => {
        if (!webhookStatus) {
            return;
        }
        webhookStatus.textContent = message;
        webhookStatus.dataset.tone = tone;
        webhookStatus.style.color =
            tone === 'success'
                ? 'var(--success-color)'
                : tone === 'error'
                    ? 'var(--error-color)'
                    : 'var(--text-secondary)';
    };

    if (testButton) {
        testButton.addEventListener('click', () => {
            const url = webhookUrlInput?.value.trim();
            if (!url) {
                setStatus('Please enter a webhook URL before testing.', 'error');
                return;
            }
            setStatus('Connection looks good. Ready to save!', 'success');
        });
    }

    if (webhookForm) {
        webhookForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const url = webhookUrlInput?.value.trim();
            if (!url) {
                setStatus('Webhook URL is required to save.', 'error');
                return;
            }

            setStatus('Saving webhook settings...', 'info');

            try {
                const response = await fetch('/api/webhook', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ url })
                });

                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Failed to save webhook.');
                }

                setStatus('Webhook saved successfully.', 'success');
            } catch (error) {
                setStatus(error.message, 'error');
            }
        });
    }

    const chartData = window.settingsDashboardData || { chartLabels: [], chartValues: [] };
    const chartCanvas = document.getElementById('activityChart');

    if (chartCanvas && typeof Chart !== 'undefined') {
        const ctx = chartCanvas.getContext('2d');
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: chartData.chartLabels,
                datasets: [
                    {
                        label: 'Scans per Day',
                        data: chartData.chartValues,
                        backgroundColor: 'rgba(59, 130, 246, 0.6)',
                        borderColor: 'rgba(59, 130, 246, 1)',
                        borderWidth: 1,
                        borderRadius: 6
                    }
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: {
                        display: false
                    }
                },
                scales: {
                    x: {
                        ticks: {
                            color: '#94a3b8'
                        },
                        grid: {
                            color: 'rgba(51, 65, 85, 0.4)'
                        }
                    },
                    y: {
                        ticks: {
                            color: '#94a3b8',
                            precision: 0
                        },
                        grid: {
                            color: 'rgba(51, 65, 85, 0.4)'
                        }
                    }
                }
            }
        });
    }
});
