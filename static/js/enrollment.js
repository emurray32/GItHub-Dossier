/**
 * Unified Enrollment Module
 *
 * Single source of truth for the Find Email → Review → Select Sequence → Enroll flow.
 * Used by Contributors, Report, Scorecard, and Accounts pages.
 *
 * API endpoints used:
 *   - POST /api/apollo-lookup        (find email)
 *   - GET  /api/sequence-mappings/enabled  (list sequences)
 *   - POST /api/apollo/enroll-sequence     (enroll in sequence)
 */
(function () {
    'use strict';

    // ── Sequence cache (shared across all enrollment panels on the page) ──
    var _seqCache = null;
    var _seqLoading = false;
    var _seqCallbacks = [];

    function loadSequences() {
        if (_seqCache) return Promise.resolve(_seqCache);

        if (_seqLoading) {
            return new Promise(function (resolve) {
                _seqCallbacks.push(resolve);
            });
        }

        _seqLoading = true;
        return fetch('/api/sequence-mappings/enabled')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                _seqCache = (data.sequences || data.mappings || []);
                _seqCallbacks.forEach(function (cb) { cb(_seqCache); });
                _seqCallbacks = [];
                return _seqCache;
            })
            .catch(function (err) {
                console.error('[Enrollment] Failed to fetch sequence mappings:', err);
                _seqCache = [];
                _seqCallbacks.forEach(function (cb) { cb(_seqCache); });
                _seqCallbacks = [];
                return _seqCache;
            })
            .finally(function () { _seqLoading = false; });
    }

    // ── Populate a <select> element with sequence options ──
    function populateSequenceSelect(selectEl) {
        if (!selectEl) return;
        selectEl.innerHTML = '<option value="">Loading sequences...</option>';

        loadSequences().then(function (sequences) {
            if (!sequences || !sequences.length) {
                selectEl.innerHTML = '<option value="">No sequences available</option>';
                return;
            }
            var html = '<option value="">Select a sequence...</option>';
            sequences.forEach(function (seq) {
                var seqId = seq.sequence_id || seq.id;
                var seqName = seq.sequence_name || seq.name || 'Unnamed';
                var steps = seq.num_steps || 0;
                html += '<option value="' + escapeAttr(seqId) + '" data-name="' + escapeAttr(seqName) + '" data-steps="' + steps + '">'
                    + escapeHtml(seqName) + (steps ? ' (' + steps + ' steps)' : '')
                    + '</option>';
            });
            selectEl.innerHTML = html;
        });
    }

    // ── Find Email via Apollo ──
    function findEmail(opts, callback) {
        // opts: { name, company, github_login, domain, contributor_id }
        return fetch('/api/apollo-lookup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(opts)
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (callback) callback(null, data);
            return data;
        })
        .catch(function (err) {
            var errData = { status: 'error', message: 'Network error during email lookup' };
            if (callback) callback(err, errData);
            return errData;
        });
    }

    // ── Enroll in Apollo Sequence ──
    function enrollInSequence(opts, callback) {
        // opts: { email, first_name, last_name, sequence_id, company_name,
        //         github_login, personalized_subject_1, personalized_email_1, ... }
        return fetch('/api/apollo/enroll-sequence', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(opts)
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (callback) callback(null, data);
            return data;
        })
        .catch(function (err) {
            var errData = { status: 'error', message: 'Network error during enrollment' };
            if (callback) callback(err, errData);
            return errData;
        });
    }

    // ── Initialize an enrollment panel ──
    // Attaches event handlers to a panel's buttons and fields.
    // panelEl: DOM element containing the enrollment panel
    // opts: { getContactData: fn() → { name, company, github_login, email, ... } }
    function initEnrollPanel(panelEl, opts) {
        if (!panelEl) return;
        opts = opts || {};

        var findBtn = panelEl.querySelector('.enroll-btn-find');
        var enrollBtn = panelEl.querySelector('.enroll-btn-enroll');
        var seqSelect = panelEl.querySelector('.enroll-seq-select');
        var statusEl = panelEl.querySelector('.enroll-status');
        var contactInfoEl = panelEl.querySelector('.enroll-contact-info');
        var emailInput = panelEl.querySelector('.enroll-email-input');
        var firstNameInput = panelEl.querySelector('.enroll-first-name');
        var lastNameInput = panelEl.querySelector('.enroll-last-name');

        // Load sequences into dropdown
        populateSequenceSelect(seqSelect);

        // Update step indicators
        function updateSteps(step) {
            var steps = panelEl.querySelectorAll('.enroll-step');
            steps.forEach(function (s, i) {
                s.className = 'enroll-step';
                if (i < step) s.classList.add('complete');
                else if (i === step) s.classList.add('active');
            });
        }

        // ── Find Email button handler ──
        if (findBtn) {
            findBtn.addEventListener('click', function () {
                var contactData = opts.getContactData ? opts.getContactData() : {};
                var name = contactData.name || '';
                var nameParts = name.trim().split(' ');

                findBtn.disabled = true;
                findBtn.innerHTML = '<span class="enroll-spinner"></span> Looking up...';

                findEmail({
                    name: name,
                    company: contactData.company || '',
                    github_login: contactData.github_login || '',
                    domain: contactData.domain || '',
                    contributor_id: contactData.contributor_id || ''
                }, function (err, data) {
                    if (data && data.email) {
                        var isDomainMismatch = data.status === 'domain_mismatch';

                        // Fill in fields
                        if (emailInput) emailInput.value = data.email;
                        if (firstNameInput && !firstNameInput.value) firstNameInput.value = data.name ? data.name.split(' ')[0] : nameParts[0] || '';
                        if (lastNameInput && !lastNameInput.value) lastNameInput.value = data.name ? data.name.split(' ').slice(1).join(' ') : nameParts.slice(1).join(' ') || '';

                        // Show contact info
                        if (contactInfoEl) {
                            contactInfoEl.style.display = 'flex';
                            contactInfoEl.className = 'enroll-contact-info' + (isDomainMismatch ? ' domain-mismatch' : '');
                            contactInfoEl.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg> '
                                + escapeHtml(data.email)
                                + (data.title ? ' &middot; ' + escapeHtml(data.title) : '')
                                + (isDomainMismatch ? ' <small>(domain mismatch)</small>' : '');
                        }

                        findBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="20 6 9 17 4 12"></polyline></svg> ' + escapeHtml(data.email);
                        findBtn.classList.add('found');
                        findBtn.disabled = true;

                        // Enable enroll button
                        if (enrollBtn) enrollBtn.disabled = false;
                        updateSteps(1);

                        if (opts.onEmailFound) opts.onEmailFound(data);
                    } else {
                        findBtn.innerHTML = 'Not found — try again';
                        findBtn.disabled = false;
                        if (statusEl) {
                            statusEl.className = 'enroll-status enroll-status-error';
                            statusEl.textContent = data.message || 'No email found via Apollo';
                        }
                    }
                });
            });
        }

        // ── Enroll button handler ──
        if (enrollBtn) {
            enrollBtn.addEventListener('click', function () {
                var email = emailInput ? emailInput.value.trim() : '';
                var firstName = firstNameInput ? firstNameInput.value.trim() : '';
                var lastName = lastNameInput ? lastNameInput.value.trim() : '';
                var seqOpt = seqSelect ? seqSelect.options[seqSelect.selectedIndex] : null;
                var sequenceId = seqOpt ? seqOpt.value : '';
                var sequenceName = seqOpt ? (seqOpt.dataset.name || seqOpt.textContent) : '';

                if (!email) {
                    if (statusEl) { statusEl.className = 'enroll-status enroll-status-error'; statusEl.textContent = 'Email is required'; }
                    return;
                }
                if (!sequenceId) {
                    if (statusEl) { statusEl.className = 'enroll-status enroll-status-error'; statusEl.textContent = 'Select a sequence first'; }
                    return;
                }

                var contactData = opts.getContactData ? opts.getContactData() : {};

                // Gather personalized email fields
                var payload = {
                    email: email,
                    first_name: firstName,
                    last_name: lastName,
                    sequence_id: sequenceId,
                    company_name: contactData.company || '',
                    github_login: contactData.github_login || ''
                };

                // Add personalized fields from the panel's email inputs
                var s1 = panelEl.querySelector('.enroll-subject-1');
                var s2 = panelEl.querySelector('.enroll-subject-2');
                if (s1 && s1.value) payload.personalized_subject_1 = s1.value;
                if (s2 && s2.value) payload.personalized_subject_2 = s2.value;
                for (var i = 1; i <= 4; i++) {
                    var el = panelEl.querySelector('.enroll-email-' + i);
                    if (el && el.value) payload['personalized_email_' + i] = el.value;
                }

                enrollBtn.disabled = true;
                enrollBtn.innerHTML = '<span class="enroll-spinner"></span> Enrolling...';
                if (statusEl) { statusEl.className = 'enroll-status enroll-status-pending'; statusEl.textContent = 'Enrolling in ' + sequenceName + '...'; }

                enrollInSequence(payload, function (err, data) {
                    if (data && data.status === 'success') {
                        enrollBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="20 6 9 17 4 12"></polyline></svg> Enrolled!';
                        enrollBtn.classList.add('enrolled');
                        if (statusEl) { statusEl.className = 'enroll-status enroll-status-success'; statusEl.textContent = 'Enrolled in ' + sequenceName; }
                        updateSteps(3);

                        if (opts.onEnrolled) opts.onEnrolled(data, sequenceName);
                    } else {
                        // User-friendly error messages
                        var msg = (data && data.message) || 'Enrollment failed';
                        var rawMsg = msg.toLowerCase();
                        if (rawMsg.includes('422') || rawMsg.includes('unprocessable')) {
                            msg = 'Invalid email — find a valid email first.';
                        } else if (rawMsg.includes('already') || rawMsg.includes('duplicate')) {
                            msg = 'Already enrolled in this sequence.';
                        } else if (rawMsg.includes('401') || rawMsg.includes('unauthorized')) {
                            msg = 'Apollo API key is invalid or expired.';
                        }

                        enrollBtn.disabled = false;
                        enrollBtn.innerHTML = 'Approve & Enroll';
                        if (statusEl) { statusEl.className = 'enroll-status enroll-status-error'; statusEl.textContent = msg; }
                    }
                });
            });
        }

        // When sequence is selected, update step indicator
        if (seqSelect) {
            seqSelect.addEventListener('change', function () {
                if (seqSelect.value && emailInput && emailInput.value) {
                    updateSteps(2);
                }
            });
        }

        // Initial step state
        updateSteps(0);

        return {
            findBtn: findBtn,
            enrollBtn: enrollBtn,
            seqSelect: seqSelect,
            populateSequences: function () { populateSequenceSelect(seqSelect); },
            reset: function () {
                if (findBtn) { findBtn.disabled = false; findBtn.classList.remove('found'); findBtn.innerHTML = 'Find Email'; }
                if (enrollBtn) { enrollBtn.disabled = true; enrollBtn.classList.remove('enrolled'); enrollBtn.innerHTML = 'Approve & Enroll'; }
                if (emailInput) emailInput.value = '';
                if (firstNameInput) firstNameInput.value = '';
                if (lastNameInput) lastNameInput.value = '';
                if (contactInfoEl) { contactInfoEl.style.display = 'none'; contactInfoEl.innerHTML = ''; }
                if (statusEl) { statusEl.className = 'enroll-status'; statusEl.textContent = ''; }
                updateSteps(0);
            }
        };
    }

    // ── Utility: escape HTML ──
    function escapeHtml(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function escapeAttr(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // ── Export ──
    window.Enrollment = {
        init: initEnrollPanel,
        findEmail: findEmail,
        enrollInSequence: enrollInSequence,
        loadSequences: loadSequences,
        populateSelect: populateSequenceSelect
    };
})();
