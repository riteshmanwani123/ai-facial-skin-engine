/* clinic.js — patient records, clinician verification (learning loop) and model-learning panel */
(function () {
    const SKIN_TYPES = ['dry', 'oily', 'normal', 'combination'];
    const VERIFY_ATTRS = ['acne_severity', 'redness_severity', 'pigmentation_melasma', 'excessive_oil',
        'open_pores', 'dehydration', 'wrinkles', 'dark_circles'];
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const nice = s => String(s).replaceAll('_', ' ');
    const fmtDate = iso => iso ? new Date(iso).toLocaleString() : '—';
    const store = {
        get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
        set(k, v) { try { localStorage.setItem(k, v); } catch (e) { } }
    };
    const api = (url, opts = {}) => fetch(url, {
        headers: { 'Content-Type': 'application/json', 'X-Clinician': clinician() }, ...opts
    }).then(async r => {
        let d = {};
        const text = await r.text();
        try { d = JSON.parse(text); } catch (_) { d = { detail: text || r.statusText }; }
        if (!r.ok) throw new Error(d.error || d.detail || d.message || `Request failed (${r.status})`);
        return d;
    });
    const clinician = () => (document.getElementById('clinician-name')?.value || store.get('clarity_clinician') || 'clinician');

    // ------------------------------------------------ patient selector (scan tab)
    const select = document.getElementById('patient-select');
    window.getSelectedPatientId = () => (select && select.value) || null;

    function loadPatientOptions(selectId) {
        return api('/api/patients').then(d => {
            if (!select) return;
            const cur = selectId ?? select.value;
            select.innerHTML = '<option value="">Anonymous scan (no patient record)</option>' +
                d.patients.map(p => `<option value="${esc(p.id)}">${esc(p.full_name)}${p.mrn ? ' · MRN ' + esc(p.mrn) : ''}</option>`).join('');
            select.value = cur || '';
        });
    }

    // ------------------------------------------------ new patient modal
    const pModal = document.getElementById('patient-modal');
    document.querySelectorAll('[data-open-patient-modal]').forEach(b => b.addEventListener('click', () => {
        document.getElementById('patient-form').reset();
        document.getElementById('patient-form-error').textContent = '';
        pModal.style.display = 'flex';
    }));
    document.getElementById('close-patient-modal')?.addEventListener('click', () => pModal.style.display = 'none');
    document.getElementById('patient-form')?.addEventListener('submit', e => {
        e.preventDefault();
        const f = Object.fromEntries(new FormData(e.target).entries());
        f.consent_research = e.target.consent_research.checked ? 1 : 0;
        api('/api/patients', { method: 'POST', body: JSON.stringify(f) })
            .then(p => { pModal.style.display = 'none'; loadPatientOptions(p.id); loadPatients(); })
            .catch(err => document.getElementById('patient-form-error').textContent = err.message);
    });

    // ------------------------------------------------ verification panel (after analysis)
    window.onAnalysisComplete = function (data) {
        const box = document.getElementById('verify-panel');
        if (!box || !data.scan_id) return;
        const mem = data.learning || {};
        const memNote = mem.enabled === false ? 'Learning from user data is switched off: this result comes from the trained model alone, and nothing here is used to train it.'
            : mem.neighbours_used
            ? `Memory adjusted this result using ${mem.neighbours_used} similar verified scan(s) (weight ${Math.round(mem.blend_weight * 100)}%).`
            : `Learning memory: ${mem.memory_size || 0} verified scans.`;
        box.innerHTML = `
          <div class="vp-head"><i class="fa-solid fa-user-check"></i> Clinician verification
            <span class="vp-sub">Confirm or correct this scan — saved to the record only; the model does not learn from it. Scan ${esc(data.scan_id.slice(0, 8))} · model ${esc(data.model_version)}</span></div>
          <p class="vp-note">${esc(memNote)}</p>
          <div class="vp-grid">
            <label>Clinician<input id="clinician-name" value="${esc(store.get('clarity_clinician') || '')}" placeholder="Your name"></label>
            <label>True skin type<select id="vp-type">${SKIN_TYPES.map(t => `<option ${t === data.skin_type ? 'selected' : ''} value="${t}">${t}</option>`).join('')}</select></label>
          </div>
          <details class="vp-details"><summary>Grade severities (optional, 0–5)</summary>
            <div class="vp-sev">${VERIFY_ATTRS.map(a => `<label>${nice(a)}<input type="number" min="0" max="5" step="0.5" data-attr="${a}" placeholder="${(data.severity_scores[a] ?? '').toString()}"></label>`).join('')}</div>
          </details>
          <label class="vp-notes">Notes<input id="vp-notes" placeholder="optional"></label>
          <button class="btn btn-primary vp-btn" id="vp-submit"><i class="fa-solid fa-check"></i> Save verified label</button>
          <div class="vp-status" id="vp-status"></div>`;
        box.style.display = 'block';
        document.getElementById('vp-submit').onclick = () => {
            const name = document.getElementById('clinician-name').value.trim();
            if (!name) { document.getElementById('vp-status').textContent = 'Please enter your name.'; return; }
            store.set('clarity_clinician', name);
            const severity = {};
            box.querySelectorAll('[data-attr]').forEach(i => { if (i.value !== '') severity[i.dataset.attr] = parseFloat(i.value); });
            api(`/api/scans/${data.scan_id}/verify`, {
                method: 'POST', body: JSON.stringify({
                    verified_skin_type: document.getElementById('vp-type').value, severity,
                    notes: document.getElementById('vp-notes').value, annotator: name
                })
            }).then(r => {
                document.getElementById('vp-status').innerHTML = `<i class="fa-solid fa-circle-check"></i> Saved. ` +
                    (r.added_to_memory ? `Added to learning memory (${r.memory_size} verified scans).` :
                        `Stored in the record — not used to train the model.`) +
                    (r.retrain ? ` ${esc(r.retrain)}.` : '');
                loadLearning();
            }).catch(err => document.getElementById('vp-status').textContent = err.message);
        };
    };

    // ------------------------------------------------ patients tab
    function statTile(label, val, icon) {
        return `<div class="metric-card-stat"><div class="stat-icon"><i class="fa-solid ${icon}"></i></div>
            <div class="stat-info"><span class="stat-val">${esc(val)}</span><span class="stat-lbl">${esc(label)}</span></div></div>`;
    }

    function loadStats() {
        api('/api/dashboard/stats').then(s => {
            const el = document.getElementById('clinic-stats');
            if (!el) return;
            el.innerHTML = statTile('Patients', s.patients, 'fa-users') + statTile('Total scans', s.scans, 'fa-camera') +
                statTile('Scans today', s.scans_today, 'fa-calendar-day') + statTile('High-risk scans', s.high_risk_scans, 'fa-triangle-exclamation') +
                statTile('Clinician-verified', s.verified_scans, 'fa-user-check') + statTile('AI vs clinician disagreements', s.model_disagreements, 'fa-code-compare');
        });
    }

    function loadPatients() {
        const q = document.getElementById('patient-search')?.value || '';
        loadStats();
        return api('/api/patients' + (q ? `?q=${encodeURIComponent(q)}` : '')).then(d => {
            const tb = document.getElementById('patients-tbody');
            if (!tb) return;
            tb.innerHTML = d.patients.length ? d.patients.map(p => `
                <tr data-pid="${esc(p.id)}">
                  <td><strong>${esc(p.full_name)}</strong><div class="muted">${esc(p.mrn || '')}</div></td>
                  <td>${esc(p.sex || '—')}</td><td>${esc(p.date_of_birth || '—')}</td>
                  <td>${p.scan_count}</td><td>${p.last_score ?? '—'}</td><td>${fmtDate(p.last_scan_at)}</td>
                  <td>${p.consent_research ? '<span class="pill ok">Yes</span>' : '<span class="pill">No</span>'}</td>
                </tr>`).join('') : '<tr><td colspan="7" class="muted">No patients yet — add one with “New patient”.</td></tr>';
            tb.querySelectorAll('tr[data-pid]').forEach(tr => tr.onclick = () => showPatient(tr.dataset.pid));
        });
    }

    function trendSvg(scans) {
        const pts = scans.filter(s => s.overall_score != null).slice().reverse();
        if (pts.length < 2) return '<p class="muted">Trend appears after two or more scans.</p>';
        const W = 520, H = 120, P = 24;
        const x = i => P + i * (W - 2 * P) / (pts.length - 1), y = v => H - P - (v / 100) * (H - 2 * P);
        const d = pts.map((s, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(s.overall_score).toFixed(1)}`).join(' ');
        return `<svg viewBox="0 0 ${W} ${H}" class="trend-svg" role="img" aria-label="Skin health score over time">
            <line x1="${P}" x2="${W - P}" y1="${y(50)}" y2="${y(50)}" class="trend-grid"/>
            <path d="${d}" class="trend-line"/>
            ${pts.map((s, i) => `<circle cx="${x(i)}" cy="${y(s.overall_score)}" r="3.5" class="trend-dot"><title>${fmtDate(s.captured_at)}: ${s.overall_score}</title></circle>`).join('')}
            <text x="${P}" y="${H - 6}" class="trend-lbl">${new Date(pts[0].captured_at).toLocaleDateString()}</text>
            <text x="${W - P}" y="${H - 6}" text-anchor="end" class="trend-lbl">${new Date(pts[pts.length - 1].captured_at).toLocaleDateString()}</text>
        </svg>`;
    }

    function showPatient(pid) {
        api(`/api/patients/${pid}`).then(p => {
            const el = document.getElementById('patient-detail');
            el.style.display = 'block';
            el.innerHTML = `
              <div class="pd-head"><div><h3>${esc(p.full_name)}</h3>
                <div class="muted">${esc([p.mrn && 'MRN ' + p.mrn, p.sex, p.date_of_birth, p.phone, p.email].filter(Boolean).join(' · '))}</div></div>
                <div class="pd-actions">
                  <label class="consent-toggle"><input type="checkbox" id="pd-consent" ${p.consent_research ? 'checked' : ''}> Research consent</label>
                  <button class="btn btn-secondary" id="pd-scan">New scan</button></div></div>
              <h4>Skin health score over time</h4>${trendSvg(p.scans)}
              <h4>Scan history</h4>
              <div class="table-wrap"><table class="clinic-table"><thead><tr><th></th><th>Date</th><th>AI skin type</th><th>Verified</th><th>Score</th><th>Triage</th><th>Model</th></tr></thead>
              <tbody>${p.scans.map(s => `<tr>
                <td><img class="thumb" loading="lazy" src="/api/scans/${esc(s.id)}/image" alt=""></td>
                <td>${fmtDate(s.captured_at)}</td><td>${esc(s.skin_type_pred)} <span class="muted">${Math.round((s.skin_type_conf || 0) * 100)}%</span></td>
                <td>${s.verified_skin_type ? esc(s.verified_skin_type) + (s.verified_skin_type !== s.skin_type_pred ? ' <span class="pill warn">corrected</span>' : ' <span class="pill ok">✓</span>') : '<span class="muted">—</span>'}</td>
                <td>${s.overall_score ?? '—'}</td>
                <td>${s.triage_status === 'HIGH_RISK_CLINICAL' ? '<span class="pill danger">High risk</span>' : '<span class="pill">' + esc(nice(s.triage_status || '').toLowerCase()) + '</span>'}</td>
                <td>${esc(s.model_version || '')}</td></tr>`).join('') || '<tr><td colspan="7" class="muted">No scans yet.</td></tr>'}</tbody></table></div>`;
            document.getElementById('pd-consent').onchange = e =>
                api(`/api/patients/${pid}`, { method: 'PATCH', body: JSON.stringify({ consent_research: e.target.checked ? 1 : 0 }) }).then(loadPatients);
            document.getElementById('pd-scan').onclick = () => {
                loadPatientOptions(pid).then(() => document.querySelector('[data-tab="scan-tab"]').click());
            };
            el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    }

    let searchTimer;
    document.getElementById('patient-search')?.addEventListener('input', () => {
        clearTimeout(searchTimer); searchTimer = setTimeout(loadPatients, 250);
    });

    // ------------------------------------------------ learning panel (metrics tab)
    function loadLearning() {
        api('/api/learning/status').then(s => {
            const el = document.getElementById('learning-panel');
            if (!el) return;
            if (s.learning_enabled === false) {
                el.innerHTML = `<div class="note-box" style="margin-top:0;"><strong>Learning from user data is switched off.</strong> Scans, doctor verdicts and photos are never used to train or adjust the model.
                  Active model: <strong>v${esc(s.active_model)}</strong>. Doctor and clinician verdicts are still saved in each patient's record.</div>`;
                return;
            }
            const pct = Math.min(100, Math.round(100 * s.verified_since_last_training / s.auto_retrain_threshold));
            const lr = s.last_run;
            el.innerHTML = `
              <div class="lp-row"><div><div class="muted">Active model</div><strong>v${esc(s.active_model)}</strong> ${s.model_kind === 'v1' ? '<span class="pill warn">legacy — run train_v2.py</span>' : ''}</div>
                <div><div class="muted">Instant-learning memory</div><strong>${s.memory_size}</strong> verified scans</div>
                <div><div class="muted">Last retrain</div>${lr ? `${fmtDate(lr.finished_at || lr.started_at)} — ${lr.status === 'running' ? 'running…' : (lr.promoted ? 'promoted v' + esc(lr.new_version) : esc(lr.reason || lr.status))}` : 'never'}</div></div>
              <div class="muted" style="margin-top:12px">New verified scans toward next automatic retrain: ${s.verified_since_last_training} / ${s.auto_retrain_threshold}</div>
              <div class="sev-bar-track" style="margin:6px 0 12px"><div class="sev-bar-fill" style="width:${pct}%"></div></div>
              <button class="btn btn-secondary" id="lp-retrain" ${s.retrain_running || s.model_kind !== 'v2' ? 'disabled' : ''}>
                <i class="fa-solid fa-rotate"></i> ${s.retrain_running ? 'Retraining…' : 'Retrain now'}</button>
              <details style="margin-top:12px"><summary>Model versions (${s.models.length})</summary>
                <table class="clinic-table"><thead><tr><th>Version</th><th>Created</th><th>Test macro-F1</th><th>Status</th><th>Notes</th></tr></thead><tbody>
                ${s.models.map(m => `<tr><td>v${esc(m.version)}</td><td>${fmtDate(m.created_at)}</td><td>${m.metrics?.test?.macro_f1_score ?? '—'}</td>
                  <td>${m.is_champion ? '<span class="pill ok">champion</span>' : ''}</td><td class="muted">${esc(m.notes || '')}</td></tr>`).join('')}
                </tbody></table></details>`;
            const b = document.getElementById('lp-retrain');
            if (b) b.onclick = () => api('/api/learning/retrain', { method: 'POST', body: '{"force":true}' }).then(loadLearning);
        });
    }

    document.querySelectorAll('.nav-btn').forEach(b => b.addEventListener('click', () => {
        if (b.dataset.tab === 'patients-tab') loadPatients();
        if (b.dataset.tab === 'metrics-tab') loadLearning();
    }));
    // Only admins use this page. Anyone else is redirected by dashboard-guard.js, so don't fire API calls for them.
    fetch('/api/auth/me', { credentials: 'include' }).then(r => (r.ok ? r.json() : null)).then(u => {
        if (!u || u.role !== 'admin') return;
        loadPatientOptions();
        loadLearning();
        setInterval(() => { if (document.getElementById('metrics-tab')?.style.display !== 'none') loadLearning(); }, 15000);
    }).catch(() => { });
})();
