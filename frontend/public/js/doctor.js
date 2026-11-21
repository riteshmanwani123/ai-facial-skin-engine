/* doctor.js — case queue, AI-drafted report form, follow-up tracking, and history. */
(function () {
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const nice = s => String(s).replaceAll('_', ' ').replace(/^\w/, c => c.toUpperCase());
    const fmtDate = iso => iso ? new Date(iso).toLocaleString() : '—';
    const api = (url, opts = {}) => fetch(url, { credentials: 'include', headers: { 'Content-Type': 'application/json' }, ...opts })
        .then(async r => { const d = await r.json(); if (!r.ok) throw new Error(d.error || d.detail || r.statusText); return d; });

    // ------------------------------------------------------------- tabs
    const tabs = document.querySelectorAll('.nav-btn[data-tab]');
    function showTab(id) {
        document.querySelectorAll('.tab-pane').forEach(p => p.style.display = p.id === id ? 'block' : 'none');
        tabs.forEach(b => b.classList.toggle('active', b.dataset.tab === id));
    }
    tabs.forEach(b => b.addEventListener('click', () => {
        showTab(b.dataset.tab);
        if (b.dataset.tab === 'queue-tab') loadQueue();
        if (b.dataset.tab === 'appts-tab') loadAppts();
        if (b.dataset.tab === 'followups-tab') loadFollowups();
        if (b.dataset.tab === 'history-tab') loadHistory();
    }));

    function concernList(sev, max = 4) {
        return Object.entries(sev || {}).filter(([, v]) => v >= 2.0).sort((a, b) => b[1] - a[1]).slice(0, max);
    }

    // ------------------------------------------------------------- queue
    function loadQueue() {
        api('/api/consultations?status=awaiting_doctor').then(d => {
            document.getElementById('queue-count').textContent = d.consultations.length || '';
            const grid = document.getElementById('queue-grid');
            grid.innerHTML = d.consultations.length ? d.consultations.map(c => queueCard(c)).join('')
                : '<p class="empty-note">No cases waiting right now.</p>';
            grid.querySelectorAll('[data-open]').forEach(btn => btn.onclick = () => openCase(btn.dataset.open, false));
        });
    }

    // ------------------------------------------------------------- appointments
    function apptChip(a) {
        return a && a.status === 'booked' ? `<span class="appt-chip">${esc(a.day_label)} · ${esc(a.start_label)} ${esc(a.tz_label)}</span>` : '<span class="appt-chip none">No time booked</span>';
    }
    function loadAppts() {
        const all = document.getElementById('appts-all').checked ? 1 : 0;
        api(`/api/appointments?all=${all}`).then(d => {
            const list = document.getElementById('appts-list');
            document.getElementById('appts-count').textContent = d.appointments.length || '';
            if (!d.appointments.length) { list.innerHTML = '<p class="empty-note">No upcoming appointments.</p>'; return; }
            const byDay = {};
            d.appointments.forEach(a => (byDay[a.day_label] = byDay[a.day_label] || []).push(a));
            list.innerHTML = Object.entries(byDay).map(([day, items]) => `<div class="appt-day"><h4>${esc(day)}</h4>${items.map(a => `
                <div class="appt-row"><div><div class="ar-time">${esc(a.time_label)}</div><div class="ar-sub">${esc(a.doctor_name || '')}</div></div>
                    <div>${esc(a.patient_name || 'Patient')}</div>
                    <button class="btn btn-secondary btn-small" data-open-case="${esc(a.consultation_id)}">Open case</button></div>`).join('')}</div>`).join('');
            list.querySelectorAll('[data-open-case]').forEach(b => b.onclick = () => {
                api(`/api/consultations/${b.dataset.openCase}`).then(con => openCase(con.id, con.status !== 'awaiting_doctor')).catch(e => alert(e.message));
            });
        });
    }
    document.getElementById('appts-all')?.addEventListener('change', loadAppts);

    function queueCard(c) {
        const p = c.patient || {};
        return `<div class="queue-card">
            <img class="qc-thumb" loading="lazy" src="/api/scans/${esc(c.scan_id)}/image" onerror="this.onerror=null; this.src='/images/clarity_logo.jpg';" alt="">
            <div class="qc-head"><span class="qc-name">${esc(p.full_name || 'Unknown')}</span><span class="pill">$${c.fee_usd}</span></div>
            <div class="qc-meta">${apptChip(c.appointment)}</div>
            <div class="qc-meta">${esc([p.age && p.age + ' yrs', p.sex, 'Submitted ' + fmtDate(c.created_at)].filter(Boolean).join(' · '))}</div>
            ${p.previous_skin_condition ? `<div class="qc-meta">History: ${esc(p.previous_skin_condition)}</div>` : ''}
            ${c.ai_suggestions ? `<div class="qc-meta">AI draft: ${esc((c.ai_suggestions.symptoms || '').slice(0, 90))}</div>` : ''}
            <button class="btn btn-primary btn-small" data-open="${esc(c.id)}"><i class="fa-solid fa-stethoscope"></i> Review &amp; diagnose</button>
        </div>`;
    }

    // ------------------------------------------------------------- follow-ups due
    function loadFollowups() {
        api('/api/consultations/pending-followups').then(d => {
            document.getElementById('followups-count').textContent = d.consultations.length || '';
            const grid = document.getElementById('followups-grid');
            grid.innerHTML = d.consultations.length ? d.consultations.map(c => {
                const p = c.patient || {}, cp = c.due_checkpoint;
                return `<div class="queue-card">
                    <div class="qc-head"><span class="qc-name">${esc(p.full_name || 'Unknown')}</span><span class="pill warn">${esc(cp.label)} scan open</span></div>
                    <div class="qc-meta">Window: ${esc(cp.opens_label)} – ${esc(cp.closes_label)}</div>
                    <div class="qc-meta">Diagnosis: ${esc(c.diagnosis || '—')}</div>
                    <button class="btn btn-secondary btn-small" data-open="${esc(c.id)}"><i class="fa-solid fa-eye"></i> View case</button>
                </div>`;
            }).join('') : '<p class="empty-note">No check-in scans are waiting on patients right now.</p>';
            grid.querySelectorAll('[data-open]').forEach(btn => btn.onclick = () => openCase(btn.dataset.open, true));
        });
    }

    // ------------------------------------------------------------- history
    function loadHistory() {
        api('/api/consultations?status=completed').then(d => {
            const tb = document.getElementById('history-tbody');
            tb.innerHTML = d.consultations.length ? d.consultations.map(c => `
                <tr data-open="${esc(c.id)}" style="cursor:pointer;">
                    <td>${esc((c.patient || {}).full_name || '—')}</td>
                    <td>${fmtDate(c.completed_at)}</td>
                    <td class="muted">${esc((c.diagnosis || '').slice(0, 60))}</td>
                    <td>${(c.checkpoints || []).map(k => `<span class="pill ${k.status === 'done' ? 'ok' : k.status === 'available' ? 'warn' : k.status === 'missed' ? 'danger' : ''}" title="${esc(k.opens_label)}">${k.day}</span>`).join(' ')}</td>
                    <td>${(c.checkpoints || []).some(k => k.status === 'done') ? '<span class="pill ok">View progress</span>' : '<span class="muted">No check-ins yet</span>'}</td>
                </tr>`).join('') : '<tr><td colspan="5" class="empty-note">No completed consultations yet.</td></tr>';
            tb.querySelectorAll('[data-open]').forEach(tr => tr.onclick = () => openCase(tr.dataset.open, true));
        });
    }

    // The sign-up questionnaire the patient answered (goals, skin type, treatments tried, sensitivities, pregnancy...).
    function questionnaireHtml(p) {
        const rows = (p && p.intake_display) || [];
        if (!rows.length) return '';
        return `<div class="questionnaire"><h4>Patient questionnaire</h4><dl>${rows.map(r => `<dt>${esc(r.label)}</dt><dd>${esc(r.value)}</dd>`).join('')}</dl></div>`;
    }

    // ------------------------------------------------------------- case report (fill or read-only)
    let currentCaseId = null;
    function openCase(cid, readonly) {
        api(`/api/consultations/${cid}`).then(con => {
            currentCaseId = cid;
            const p = con.patient || {}, scan = con.scan || {};
            const concerns = concernList(scan.severity);
            document.getElementById('case-summary').innerHTML = `
                <div class="report-score-row">
                    <img class="qc-thumb" style="width:140px;" src="/api/scans/${esc(con.scan_id)}/image" onerror="this.onerror=null; this.src='/images/clarity_logo.jpg';" alt="">
                    <div>
                        <div style="font-weight:700; font-size:16px;">${esc(p.full_name || '')} — ${esc(p.age || '?')} yrs, ${esc(p.sex || 'unspecified')}${p.mrn ? ` <span class="tag">${esc(p.mrn)}</span>` : ''}</div>
                        <div class="muted">AI: ${esc(scan.skin_type_pred)} (${Math.round((scan.skin_type_conf || 0) * 100)}%) · score ${scan.overall_score}/100 · triage ${esc(scan.triage_status || '')}</div>
                        <div style="margin-top:6px;">${apptChip(con.appointment)}</div>
                        ${p.previous_skin_condition && !(p.intake_display || []).length ? `<div class="muted">Patient-reported history: ${esc(p.previous_skin_condition)}</div>` : ''}
                    </div>
                </div>
                <div class="concerns-list">${concerns.map(([a, v]) => `<div class="concern-chip">${esc(nice(a))} — ${v.toFixed(1)}/5.0</div>`).join('') || '<p class="muted">No significant AI-flagged concerns.</p>'}</div>
                ${questionnaireHtml(p)}
                <div id="case-views"></div>
                ${con.checkpoints ? renderCheckpointsReadonly(con.checkpoints) : ''}
            `;
            loadCaseViews(con.scan_id);

            const form = document.getElementById('report-form');
            const draft = con.ai_suggestions || {};
            form.symptoms.value = con.symptoms || draft.symptoms || '';
            form.diagnosis.value = con.diagnosis || draft.diagnosis || '';
            form.cure.value = con.cure || draft.cure || '';
            form.prescription.value = con.prescription || draft.prescription || '';
            form.previous_condition.value = con.previous_condition || draft.previous_condition || p.previous_skin_condition || '';
            form.verified_skin_type.value = con.verified_skin_type || scan.skin_type_pred || 'normal';

            const sevGrid = document.getElementById('sev-mini-grid');
            const sevSource = con.verified_severity || scan.severity || {};
            sevGrid.innerHTML = Object.entries(scan.severity || {}).map(([a, v]) => `
                <label>${esc(nice(a))}<input type="number" min="0" max="5" step="0.1" data-attr="${esc(a)}" value="${(sevSource[a] ?? v).toFixed(1)}"></label>`).join('');

            const submitBtn = form.querySelector('button[type=submit]');
            form.querySelectorAll('input, textarea, select').forEach(el => el.disabled = readonly);
            submitBtn.style.display = readonly ? 'none' : 'block';
            document.getElementById('report-error').textContent = '';
            showTab('report-tab');
        }).catch(err => alert(err.message));
    }

    const VIEW_LABELS = { front: 'Front', left: 'Left cheek', right: 'Right cheek', up: 'Chin & neck', down: 'Forehead' };
    function loadCaseViews(scanId) {
        api(`/api/scans/${scanId}/views`).then(d => {
            const box = document.getElementById('case-views');
            if (!box || !d.views || d.views.length < 2) return; // single-photo (older) scans keep the original look
            box.innerHTML = `<h3 style="font-size:13px; margin:16px 0 4px;"><i class="fa-solid fa-camera"></i> Captured angles <span class="muted" style="font-weight:400;">(patient's eyes covered before storage — click to enlarge)</span></h3>
                <div class="views-gallery">${d.views.map(v => `<a href="${esc(v.url)}" target="_blank" rel="noopener">
                    <img src="${esc(v.url)}" onerror="this.onerror=null; this.src='/images/clarity_logo.jpg';" alt="${esc(VIEW_LABELS[v.view] || v.view)}" loading="lazy"><span>${esc(VIEW_LABELS[v.view] || v.view)}</span></a>`).join('')}</div>`;
        }).catch(() => { });
    }

    function renderCheckpointsReadonly(cps) {
        const chip = c => c.status === 'done' ? '<span class="pill ok">Done</span>' : c.status === 'available' ? '<span class="pill warn">Open now</span>' : c.status === 'missed' ? '<span class="pill danger">Missed</span>' : `<span class="pill">Opens ${esc(c.opens_label)}</span>`;
        const done = cps.filter(c => c.comparison);
        return `<h3 style="font-size:13px; margin:16px 0 6px;">Check-in scans (day 15 / 30 / 45)</h3>
            <div class="queue-meta" style="display:flex; gap:14px; flex-wrap:wrap;">${cps.map(c => `<span>${esc(c.label)} ${chip(c)}</span>`).join('')}</div>
            ${done.map(c => { const cmp = c.comparison, rows = Object.entries(cmp.per_attribute || {}).filter(([, v]) => Math.abs(v.delta) > 0.05);
                return `<h4 style="margin:14px 0 4px; font-size:13px;">${esc(c.label)} vs first scan</h4><div class="compare-grid">
                    <div class="compare-card">Overall score: ${cmp.score_before} → ${cmp.score_after}</div>
                    ${rows.map(([a, v]) => `<div class="compare-card">${esc(nice(a))}: ${v.before.toFixed(1)} → ${v.after.toFixed(1)}</div>`).join('')}</div>`; }).join('')}`;
    }

    document.getElementById('back-to-queue').addEventListener('click', () => { showTab('queue-tab'); loadQueue(); });

    document.getElementById('report-form').addEventListener('submit', e => {
        e.preventDefault();
        const f = document.getElementById('report-form');
        const verified_severity = {};
        document.querySelectorAll('#sev-mini-grid [data-attr]').forEach(i => { if (i.value !== '') verified_severity[i.dataset.attr] = parseFloat(i.value); });
        const body = {
            symptoms: f.symptoms.value, diagnosis: f.diagnosis.value, cure: f.cure.value,
            prescription: f.prescription.value, previous_condition: f.previous_condition.value,
            verified_skin_type: f.verified_skin_type.value,
            verified_severity,
        };
        api(`/api/consultations/${currentCaseId}`, { method: 'PATCH', body: JSON.stringify(body) })
            .then(() => { showTab('queue-tab'); loadQueue(); loadFollowups(); })
            .catch(err => document.getElementById('report-error').textContent = err.message);
    });

    // ------------------------------------------------------------- auth gate
    (async function init() {
        const user = await window.requireAuth('clinician');
        if (!user) {
            const grid = document.getElementById('queue-grid');
            if (grid) {
                grid.innerHTML = `<div class="note-box" style="margin:20px;text-align:center;background:#fef2f2;border:1px solid #fecaca;padding:24px;border-radius:12px;">
                    <h3 style="color:#991b1b;margin-bottom:8px;"><i class="fa-solid fa-user-doctor"></i> Doctor Login Required</h3>
                    <p style="color:#7f1d1d;margin-bottom:16px;">You are currently signed in with a Patient account (or not signed in). Sign in as Doctor to review cases in the queue.</p>
                    <a href="/login?as=doctor" class="btn btn-primary" style="display:inline-block;padding:10px 20px;font-weight:600;text-decoration:none;">
                        Sign In as Doctor (Dr. Tanya Unni)
                    </a>
                </div>`;
            }
            return;
        }
        const bar = document.getElementById('session-bar');
        if (bar) bar.innerHTML = `${esc(user.full_name)} · ID ${esc(user.user_code || '')} · <a href="#" id="logout-link">Sign out</a>`;
        document.getElementById('logout-link')?.addEventListener('click', e => { e.preventDefault(); window.clarityLogout(); });
        loadQueue();
        api('/api/appointments').then(d => { document.getElementById('appts-count').textContent = d.appointments.length || ''; }).catch(() => { });
    })();
})();
