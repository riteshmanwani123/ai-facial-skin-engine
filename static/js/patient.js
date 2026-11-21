/* patient.js — patient self-service portal (sign in required): live scan -> AI report ->
   paid doctor consultation -> doctor's plan -> 30-day follow-up scan & comparison. */
(function () {
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const nice = s => String(s).replaceAll('_', ' ').replace(/^\w/, c => c.toUpperCase());
    const fmtDate = iso => iso ? new Date(iso).toLocaleString() : '—';
    const api = (url, opts = {}) => fetch(url, { credentials: 'include', headers: { 'Content-Type': 'application/json' }, ...opts })
        .then(async r => {
            let d = {};
            const text = await r.text();
            try { d = JSON.parse(text); } catch (_) { d = { detail: text || r.statusText }; }
            if (!r.ok) throw new Error(d.error || d.detail || d.message || `Request failed (${r.status})`);
            return d;
        });

    const state = {
        patientId: null, patientName: '',
        consultationId: null,          // set from the server (/api/me/journey) — the server decides where the patient is
        scanId: null, followupMode: false, followupDay: null, pollTimer: null, journey: null,
    };

    const STEPS = ['scan', 'report', 'payment', 'pending', 'completed'];
    function showStep(name) {
        STEPS.forEach(s => document.getElementById('step-' + s).classList.toggle('active', s === name));
        document.querySelectorAll('.wp-step').forEach(el => {
            const i = STEPS.indexOf(el.dataset.step), cur = STEPS.indexOf(name);
            el.classList.toggle('active', el.dataset.step === name);
            el.classList.toggle('done', i < cur);
        });
        window.scrollTo({ top: 0, behavior: 'smooth' });
        if (name === 'payment' && typeof openPayment === 'function') openPayment();
    }

    // ---------------------------------------------------------------- step 1: guided, eye-safe scan
    // Five angles. Every photo is redacted in the browser (EyeSafeCapture) before it exists as an image
    // we can save or send: the live preview already shows the eyes covered, and capture reads from that.
    const VIEWS = [
        { key: 'front', label: 'Front', arrow: '●', title: 'Look straight at the camera', hint: 'Keep your face centred and relaxed. The photo is taken automatically.', required: true },
        { key: 'left', label: 'Left cheek', arrow: '→', title: 'Turn your head to your right', hint: 'So your LEFT cheek faces the camera. Hold it there — we take the photo for you.' },
        { key: 'right', label: 'Right cheek', arrow: '←', title: 'Turn your head to your left', hint: 'So your RIGHT cheek faces the camera. Hold it there — we take the photo for you.' },
        { key: 'up', label: 'Chin & neck', arrow: '↑', title: 'Tilt your head back', hint: 'Lift your chin so your chin and neck are visible. Hold it there — we take the photo for you.' },
        { key: 'down', label: 'Forehead', arrow: '↓', title: 'Tilt your head down', hint: 'Lower your chin so your forehead is clearly visible. Hold it there — we take the photo for you.' },
    ];
    const FORCE_AFTER_MS = 5000; // the camera takes each photo by itself; if the angle still isn't reached after this long, the manual button also works anyway

    const els = {
        video: document.getElementById('cam-video'), live: document.getElementById('cam-live'),
        start: document.getElementById('cam-start'), capture: document.getElementById('cam-capture'),
        skip: document.getElementById('cam-skip'), analyze: document.getElementById('cam-analyze'),
        status: document.getElementById('cam-status'), err: document.getElementById('cam-error'),
        chips: document.getElementById('view-chips'), prompt: document.getElementById('capture-prompt'),
        file: document.getElementById('file-fallback'),
    };
    let session = null, forceMode = false, mismatchSince = null;
    state.views = {};      // key -> { dataUrl, polygons, width, height, poseOk }
    state.skipped = {};    // key -> true
    state.current = 'front';

    const pending = () => VIEWS.find(v => !state.views[v.key] && !state.skipped[v.key]);
    const viewDef = key => VIEWS.find(v => v.key === key);

    function renderChips() {
        els.chips.innerHTML = VIEWS.map(v => {
            const done = state.views[v.key], skipped = state.skipped[v.key], cur = state.current === v.key && !done;
            const cls = done ? 'done' : skipped ? 'skipped' : cur ? 'current' : '';
            return `<div class="view-chip ${cls}" data-view="${v.key}" title="${done ? 'Tap to retake' : ''}">
                ${done ? `<img class="vc-thumb" src="${done.dataUrl}" alt="${esc(v.label)}">` : `<div class="vc-thumb"></div>`}
                <span>${done ? '✓ ' : ''}${esc(v.label)}</span>${done ? '<span class="vc-retake">tap to retake</span>' : skipped ? '<span class="vc-retake">skipped</span>' : ''}</div>`;
        }).join('');
        els.chips.querySelectorAll('.view-chip.done, .view-chip.skipped').forEach(c => c.onclick = () => {
            const k = c.dataset.view; delete state.views[k]; delete state.skipped[k]; setCurrent(k);
        });
    }

    function setCurrent(key) {
        state.current = key; forceMode = false; mismatchSince = null;
        const v = viewDef(key);
        if (v) {
            els.prompt.innerHTML = `<div class="cp-title"><span class="cp-arrow">${v.arrow}</span>${esc(v.title)}</div><div class="cp-hint">${esc(v.hint)}</div>`;
            if (session) session.setTarget(key);
        }
        els.skip.style.display = session && v && !v.required ? 'inline-flex' : 'none';
        refreshButtons();
        renderChips();
    }

    function refreshButtons() {
        const allDone = !pending();
        els.analyze.style.display = allDone && state.views.front ? 'inline-flex' : 'none';
        els.capture.style.display = session && !allDone ? 'inline-flex' : 'none';
        els.skip.style.display = session && !allDone && !viewDef(state.current).required ? 'inline-flex' : 'none';
        if (allDone) { els.prompt.innerHTML = `<div class="cp-title">All set — review the photos above</div><div class="cp-hint">Tap any photo to retake it, or analyze your skin now.</div>`; }
    }

    function setStatus(text, cls) { els.status.textContent = text; els.status.className = 'cam-status ' + (cls || ''); }

    const wrap = document.getElementById('camera-wrap');
    let progressEl = null;
    function setProgress(p) {
        if (!wrap) return;
        if (!progressEl) { progressEl = document.createElement('div'); progressEl.className = 'cam-progress'; progressEl.innerHTML = '<div></div>'; wrap.appendChild(progressEl); }
        progressEl.style.opacity = p > 0 ? '1' : '0'; progressEl.firstChild.style.width = Math.round(p * 100) + '%';
    }
    function flash() { if (!wrap) return; wrap.classList.remove('flash'); void wrap.offsetWidth; wrap.classList.add('flash'); }

    const FIT_TEXT = { back: 'Move a little further from the camera', closer: 'Move a little closer', centre: 'Centre your face in the frame' };

    function onLiveUpdate(s) {
        const manualLabel = '<i class="fa-solid fa-circle-dot"></i> Capture now';
        if (!s.safe) { els.capture.disabled = true; els.capture.innerHTML = manualLabel; setProgress(0); setStatus(s.reason || 'Looking for your face…', 'bad'); mismatchSince = null; return; }
        setProgress(s.progress || 0);
        if (s.poseOk) {
            mismatchSince = null; forceMode = false; els.capture.disabled = false; els.capture.innerHTML = manualLabel;
            if (s.fit === 'back' || s.fit === 'centre') setStatus('Eyes covered ✓ · ' + FIT_TEXT[s.fit], 'warn');
            else setStatus(s.progress > 0 ? 'Eyes covered ✓ · Hold still — taking the photo…' : 'Eyes covered ✓ · Angle ✓', 'ok');
        } else {
            if (mismatchSince === null) mismatchSince = performance.now();
            forceMode = performance.now() - mismatchSince > FORCE_AFTER_MS;
            els.capture.disabled = !forceMode; els.capture.innerHTML = manualLabel;
            setStatus('Eyes covered ✓ · ' + ((s.fit === 'back' || s.fit === 'centre') ? FIT_TEXT[s.fit] : (s.guide || FIT_TEXT[s.fit] || 'Adjust your angle')), 'warn');
        }
    }

    // The session found the best frame of a held angle by itself.
    function onAutoShot(shot) {
        const key = state.current, v = viewDef(key);
        if (!v || state.views[key] || !pending()) return;
        acceptShot(key, shot); flash(); setProgress(0);
        setStatus(`✓ ${v.label} captured${shot.poseOk ? '' : ' (closest angle we could get)'}`, shot.poseOk ? 'ok' : 'warn');
    }

    function acceptShot(key, shot) {
        state.views[key] = shot; delete state.skipped[key];
        els.err.textContent = '';
        const next = pending();
        setCurrent(next ? next.key : key);
        if (!next) refreshButtons();
    }

    els.start.addEventListener('click', async () => {
        els.err.textContent = '';
        els.start.disabled = true; setStatus('Loading eye-privacy protection…', 'warn');

        // Check for Secure Context (HTTPS or localhost)
        const isSecure = window.isSecureContext || location.hostname === 'localhost' || location.hostname === '127.0.0.1';
        if (!isSecure || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            const isLocalIp = location.hostname !== 'localhost' && location.hostname !== '127.0.0.1';
            if (isLocalIp) {
                els.err.innerHTML = `
                    <div style="background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 8px; padding: 14px; margin-top: 10px; text-align: left; line-height: 1.5;">
                        <strong style="color: #f87171; font-size: 14px;">⚠️ Camera Blocked: Browsers require HTTPS or localhost</strong>
                        <p style="margin: 6px 0 10px; font-size: 13px; color: var(--text-primary, #f1f5f9);">
                            Modern browsers (Safari, Chrome, Brave) disable webcam access over unencrypted HTTP on IP addresses (<code>${location.hostname}</code>) to protect user privacy.
                        </p>
                        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                            <a href="http://localhost:3001/patient" class="btn btn-primary btn-small" style="text-decoration: none; padding: 8px 12px; font-weight: 600;">
                                🚀 Open on localhost:3001 (Enables Camera)
                            </a>
                            <a href="https://${location.hostname}:3001/patient" class="btn btn-secondary btn-small" style="text-decoration: none; padding: 8px 12px;">
                                🔒 Open via HTTPS
                            </a>
                        </div>
                        <p style="margin: 10px 0 0; font-size: 12px; color: var(--text-secondary, #94a3b8);">
                            Or click &ldquo;Upload a photo for this angle&rdquo; below to test without camera.
                        </p>
                    </div>
                `;
            } else {
                els.err.textContent = 'Camera is unavailable in this browser context. Please use Chrome, Safari, or Brave on a secure connection.';
            }
            els.start.disabled = false;
            setStatus('Camera is off', 'bad');
            return;
        }

        try {
            await window.EyeSafeCapture.preload();
        } catch (e) {
            // Fail closed: without the eye-masking model we do not take camera photos at all.
            els.err.textContent = 'Eye-privacy protection could not load, so the camera is disabled. Please refresh the page and try again.';
            els.start.disabled = false; setStatus('Camera is off', 'bad'); return;
        }

        let stream;
        try {
            // Primary attempt: standard selfie camera constraints
            stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: 'user', width: { ideal: 960 }, height: { ideal: 720 } }
            });
        } catch (mediaErr) {
            // Secondary attempt: fallback to generic video if overconstrained
            try {
                stream = await navigator.mediaDevices.getUserMedia({ video: true });
            } catch (fallbackErr) {
                let helpfulMsg = 'Camera unavailable (' + fallbackErr.message + ').';
                if (fallbackErr.name === 'NotAllowedError' || fallbackErr.name === 'PermissionDeniedError') {
                    helpfulMsg = 'Camera permission was denied. Please allow camera access in your browser settings (look for the camera icon in the URL bar) and in macOS System Settings > Privacy & Security > Camera.';
                } else if (fallbackErr.name === 'NotReadableError' || fallbackErr.name === 'TrackStartError') {
                    helpfulMsg = 'Camera is currently in use by another application or tab (e.g. Google Meet, Zoom, FaceTime). Please close other camera apps and click "Turn on camera" again.';
                }
                els.err.textContent = helpfulMsg + ' You can also upload a photo for each angle below.';
                els.start.disabled = false;
                setStatus('Camera is off', 'bad');
                return;
            }
        }

        try {
            session = new window.EyeSafeCapture.LiveSession(els.video, els.live, onLiveUpdate, onAutoShot);
            session.setTarget(state.current);
            await session.start(stream);
            els.start.style.display = 'none';
            setCurrent(state.current);
        } catch (err) {
            els.err.textContent = 'Camera session failed (' + err.message + '). You can upload a photo for each angle below instead.';
            els.start.disabled = false; setStatus('Camera is off', 'bad');
        }
    });

    els.capture.addEventListener('click', () => {
        if (!session) return;
        try {
            const shot = session.capture({ force: forceMode });
            shot.poseOk = session.state.poseOk;
            acceptShot(state.current, shot);
        } catch (e) { els.err.textContent = e.message; }
    });

    els.skip.addEventListener('click', () => {
        const v = viewDef(state.current);
        if (!v || v.required) return;
        state.skipped[v.key] = true;
        const next = pending();
        setCurrent(next ? next.key : v.key);
        refreshButtons(); renderChips();
    });

    els.file.addEventListener('change', async e => {
        const file = e.target.files[0]; e.target.value = '';
        if (!file) return;
        els.err.textContent = '';
        setStatus('Covering eyes on your photo…', 'warn');
        try {
            await window.EyeSafeCapture.preload();
            const shot = await window.EyeSafeCapture.redactFile(file, state.current);
            acceptShot(state.current, shot);
            setStatus(shot.poseOk ? 'Photo added — eyes covered ✓' : `Photo added — eyes covered ✓ (it doesn't look like a "${viewDef(state.current)?.label || ''}" angle; tap its thumbnail to retake)`, shot.poseOk ? 'ok' : 'warn');
        } catch (err) {
            els.err.textContent = err.message || 'Could not use that photo';
            setStatus(session ? 'Camera on' : 'Camera is off', 'bad');
        }
    });

    function stopCamera() { if (session) { session.stop(); session = null; } setProgress(0); els.start.style.display = 'inline-flex'; els.start.disabled = false; els.capture.style.display = 'none'; els.skip.style.display = 'none'; setStatus('Camera is off'); }

    function resetCapture() {
        stopCamera(); state.views = {}; state.skipped = {}; els.analyze.style.display = 'none'; els.err.textContent = '';
        setCurrent('front');
    }

    els.analyze.addEventListener('click', () => {
        if (!state.views.front) return;
        document.getElementById('scan-loading').style.display = 'block';
        els.analyze.disabled = true;
        const extra = {}, redaction = { front: { polygons: state.views.front.polygons } };
        VIEWS.filter(v => v.key !== 'front' && state.views[v.key]).forEach(v => {
            extra[v.key] = state.views[v.key].dataUrl; redaction[v.key] = { polygons: state.views[v.key].polygons };
        });
        const body = { image_base64: state.views.front.dataUrl, extra_views: extra, client_redaction: redaction, patient_id: state.patientId };
        if (state.followupMode && state.consultationId) body.consultation_id = state.consultationId;   // the server picks the open check-in day
        api('/api/analyze', { method: 'POST', body: JSON.stringify(body) })
            .then(data => {
                document.getElementById('scan-loading').style.display = 'none';
                els.analyze.disabled = false;
                state.scanId = data.scan_id;
                const wasFollowup = state.followupMode;
                state.followupMode = false; state.followupDay = null;
                resetCapture();
                if (wasFollowup) syncJourney();                       // reload the plan with the new check-in on it
                else { renderReport(data); showStep('report'); }
            })
            .catch(err => {
                document.getElementById('scan-loading').style.display = 'none';
                els.analyze.disabled = false;
                els.err.textContent = (/^(You already|Your |A new scan)/.test(err.message) ? '' : 'Analysis failed: ') + err.message;
                if (/^(You already|Your |A new scan)/.test(err.message)) syncJourney();   // the server says this scan isn't allowed now
            });
    });

    function resetCameraUI() { resetCapture(); }
    setCurrent('front');

    // ---------------------------------------------------------------- step 2: AI report
    let lastReport = null;
    function radialGauge(score) {
        return `<div class="report-score-gauge"><svg viewBox="0 0 36 36" class="circular-chart">
            <path class="circle-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
            <path class="circle" stroke-dasharray="${score}, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
            <text x="18" y="20.35" class="percentage">${score}</text></svg>
            <div class="muted">Overall skin health index</div></div>`;
    }

    const BIOMETRIC_CATEGORIES = [
        {
            title: 'Acne & Blemishes',
            icon: 'fa-solid fa-virus-covid',
            keys: ['acne_severity', 'blackheads', 'whiteheads', 'post_acne_marks'],
            labels: {
                acne_severity: 'Acne Severity',
                blackheads: 'Blackheads',
                whiteheads: 'Whiteheads',
                post_acne_marks: 'Post-Acne Marks',
            }
        },
        {
            title: 'Pores, Oiliness & Texture',
            icon: 'fa-solid fa-droplet',
            keys: ['excessive_oil', 'open_pores', 'texture_roughness', 'dehydration'],
            labels: {
                excessive_oil: 'Excessive Oiliness',
                open_pores: 'Open Pores',
                texture_roughness: 'Skin Texture Roughness',
                dehydration: 'Skin Dehydration',
            }
        },
        {
            title: 'Skin Barrier & Sensitivity',
            icon: 'fa-solid fa-shield-heart',
            keys: ['redness_severity', 'skin_irritation', 'skin_sensitivity'],
            labels: {
                redness_severity: 'Redness / Erythema',
                skin_irritation: 'Skin Irritation',
                skin_sensitivity: 'Skin Sensitivity',
            }
        },
        {
            title: 'Tone, Melasma & Sun Damage',
            icon: 'fa-solid fa-sun',
            keys: ['pigmentation_melasma', 'freckles', 'tanning_severity'],
            labels: {
                pigmentation_melasma: 'Pigmentation / Melasma',
                freckles: 'Freckles',
                tanning_severity: 'Sun Tanning & UV Impact',
            }
        },
        {
            title: 'Aging, Lines & Elasticity',
            icon: 'fa-solid fa-clock',
            keys: ['fine_lines_eyes', 'eye_puffiness', 'dark_circles', 'wrinkles', 'skin_elasticity'],
            labels: {
                fine_lines_eyes: 'Fine Lines (Eye Area)',
                eye_puffiness: 'Under-Eye Puffiness',
                dark_circles: 'Dark Circles',
                wrinkles: 'Facial Wrinkles',
                skin_elasticity: 'Loss of Elasticity',
            }
        }
    ];

    function getBiometricStatus(val) {
        if (val < 1.5) return { label: 'Optimal', cls: 'optimal', desc: 'Healthy baseline' };
        if (val < 2.5) return { label: 'Mild', cls: 'mild', desc: 'Mild target area' };
        if (val < 3.5) return { label: 'Moderate', cls: 'moderate', desc: 'Moderate concern' };
        return { label: 'High', cls: 'high', desc: 'Clinical priority' };
    }

    function renderBiometricsHtml(scores, activeFilter) {
        activeFilter = activeFilter || 'all';
        const allEntries = Object.entries(scores || {});
        const totalCount = allEntries.length;
        const concernsCount = allEntries.filter(([, v]) => Number(v) >= 1.5).length;
        const healthyCount = allEntries.filter(([, v]) => Number(v) < 1.5).length;

        let categoriesHtml = '';
        BIOMETRIC_CATEGORIES.forEach(cat => {
            const catItems = cat.keys.filter(k => scores[k] !== undefined).map(k => ({
                key: k,
                label: cat.labels[k] || nice(k),
                val: Number(scores[k]),
                status: getBiometricStatus(Number(scores[k]))
            }));

            const filteredItems = catItems.filter(item => {
                if (activeFilter === 'concerns') return item.val >= 1.5;
                if (activeFilter === 'healthy') return item.val < 1.5;
                return true;
            });

            if (filteredItems.length === 0) return;

            categoriesHtml += `
                <div class="metric-category">
                    <div class="metric-category-title">
                        <i class="${cat.icon}" style="color: var(--accent-cyan, #00d2ff);"></i> ${esc(cat.title)}
                    </div>
                    <div class="metric-category-grid">
                        ${filteredItems.map(item => `
                            <div class="biometric-chip">
                                <div class="chip-top">
                                    <span class="chip-name">${esc(item.label)}</span>
                                    <span class="chip-score">${item.val.toFixed(1)} <span class="muted" style="font-size:10px; font-weight:normal;">/ 5.0</span></span>
                                </div>
                                <div class="chip-status-row">
                                    <span class="chip-status-badge ${item.status.cls}">${item.status.label}</span>
                                    <span class="muted" style="font-size:10px;">${esc(item.status.desc)}</span>
                                </div>
                                <div class="chip-bar">
                                    <div class="chip-fill ${item.status.cls}" style="width: ${Math.min(100, Math.max(5, (item.val / 5) * 100))}%"></div>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        });

        // Any leftover keys not in the standard 5 categories
        const categorizedKeys = new Set(BIOMETRIC_CATEGORIES.flatMap(c => c.keys));
        const extraItems = allEntries.filter(([k]) => !categorizedKeys.has(k)).map(([k, v]) => ({
            key: k, label: nice(k), val: Number(v), status: getBiometricStatus(Number(v))
        })).filter(item => {
            if (activeFilter === 'concerns') return item.val >= 1.5;
            if (activeFilter === 'healthy') return item.val < 1.5;
            return true;
        });

        if (extraItems.length > 0) {
            categoriesHtml += `
                <div class="metric-category">
                    <div class="metric-category-title"><i class="fa-solid fa-chart-simple" style="color: var(--accent-cyan, #00d2ff);"></i> Additional Biometrics</div>
                    <div class="metric-category-grid">
                        ${extraItems.map(item => `
                            <div class="biometric-chip">
                                <div class="chip-top">
                                    <span class="chip-name">${esc(item.label)}</span>
                                    <span class="chip-score">${item.val.toFixed(1)} <span class="muted" style="font-size:10px; font-weight:normal;">/ 5.0</span></span>
                                </div>
                                <div class="chip-status-row">
                                    <span class="chip-status-badge ${item.status.cls}">${item.status.label}</span>
                                    <span class="muted" style="font-size:10px;">${esc(item.status.desc)}</span>
                                </div>
                                <div class="chip-bar">
                                    <div class="chip-fill ${item.status.cls}" style="width: ${Math.min(100, Math.max(5, (item.val / 5) * 100))}%"></div>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        return `
            <div class="metrics-section">
                <div class="metrics-header">
                    <div>
                        <h3 style="font-size:15px; margin:0 0 2px; font-weight:600;">Clinical Biometric Evaluation</h3>
                        <div class="muted" style="font-size:12px;">Full diagnostic breakdown across all ${totalCount} indicators</div>
                    </div>
                    <div class="metrics-filter-pills" id="metrics-filter-bar">
                        <button type="button" class="metric-filter-btn ${activeFilter === 'all' ? 'active' : ''}" data-filter="all">
                            All Indicators (${totalCount})
                        </button>
                        <button type="button" class="metric-filter-btn ${activeFilter === 'concerns' ? 'active' : ''}" data-filter="concerns">
                            Focus Areas (${concernsCount})
                        </button>
                        <button type="button" class="metric-filter-btn ${activeFilter === 'healthy' ? 'active' : ''}" data-filter="healthy">
                            Optimal / Clear (${healthyCount})
                        </button>
                    </div>
                </div>

                <div class="score-transparency-card">
                    <strong style="color: var(--text-primary);"><i class="fa-solid fa-circle-check" style="margin-right:6px; color: var(--accent-emerald, #10b981);"></i>Complete Biometric Inclusivity:</strong>
                    Your Skin Confidence Score is computed directly from all ${totalCount} indicators evaluated across your scan. Lower severity scores (0–1.4) reflect healthy, optimal barrier; higher scores (1.5–5.0) pinpoint target areas addressed in your Dr Tanya treatment plan below.
                </div>

                <div id="metrics-categories-content">
                    ${categoriesHtml || '<p class="muted" style="padding:16px; text-align:center;">No indicators matching this view.</p>'}
                </div>
            </div>
        `;
    }

    function renderReport(data) {
        lastReport = data;
        const triageRed = data.triage && data.triage.banner_color === 'red';
        const score = data.overall_health_score ?? 75;
        const ratingText = score >= 80 ? 'Optimal Skin Vitality' : score >= 65 ? 'Good Baseline Health' : score >= 50 ? 'Moderate Baseline' : 'Target Care Recommended';

        document.getElementById('report-content').innerHTML = `
            <div class="report-triage ${triageRed ? 'red' : 'green'}">
                <strong>${esc(data.triage?.banner_title || '')}</strong>
                <p style="margin-top:6px; font-size:13px;">${esc(data.triage?.banner_desc || '')}</p>
            </div>
            <div class="report-score-row">
                ${radialGauge(score)}
                <div>
                    <div class="muted">Detected skin type</div>
                    <div style="font-size:22px; font-weight:700;">${esc((data.skin_type || '').toUpperCase())}</div>
                    <div class="muted">Confidence ${(data.skin_type_confidence * 100).toFixed(1)}% · <strong style="color: var(--text-primary);">${ratingText}</strong></div>
                    <div class="muted" style="font-size:11px; margin-top:2px;">Evaluated across all ${Object.keys(data.severity_scores || {}).length} clinical biometrics</div>
                </div>
            </div>
            <div id="biometrics-mount">
                ${renderBiometricsHtml(data.severity_scores, 'all')}
            </div>`;

        // Interactive Filter Buttons Handler
        const mount = document.getElementById('biometrics-mount');
        if (mount) {
            mount.addEventListener('click', (e) => {
                const btn = e.target.closest('.metric-filter-btn');
                if (!btn) return;
                const filter = btn.dataset.filter;
                mount.innerHTML = renderBiometricsHtml(data.severity_scores, filter);
            });
        }

        renderRecommendedProducts(data.routine);
        renderViewsGallery(data.scan_id, data.views || []);
    }

    const VIEW_LABELS = { front: 'Front', left: 'Left cheek', right: 'Right cheek', up: 'Chin & neck', down: 'Forehead' };
    function renderViewsGallery(scanId, keys) {
        const box = document.getElementById('report-views');
        if (!box) return;
        box.innerHTML = keys.length ? `<h3 style="font-size:14px; margin:20px 0 4px;"><i class="fa-solid fa-eye-slash"></i> Your scan photos <span class="muted" style="font-weight:400;">(eyes covered)</span></h3>
            <div class="views-gallery">${keys.map(k => `<a href="/api/scans/${esc(scanId)}/views/${esc(k)}/image" target="_blank" rel="noopener">
                <img src="/api/scans/${esc(scanId)}/views/${esc(k)}/image" onerror="this.onerror=null; this.src='/images/clarity_logo.jpg';" alt="${esc(VIEW_LABELS[k] || k)}" loading="lazy"><span>${esc(VIEW_LABELS[k] || k)}</span></a>`).join('')}</div>` : '';
    }

    function renderRecommendedProducts(routine) {
        const box = document.getElementById('recommended-products');
        if (!box || !routine) return;
        const timed = [
            ...(routine.am_routine || []).map(s => ({ ...s, time: 'AM' })),
            ...(routine.pm_routine || []).map(s => ({ ...s, time: 'PM' })),
            ...(routine.weekly_routine || []).map(s => ({ ...s, time: 'Weekly' })),
        ];
        const byName = {};
        timed.forEach(s => {
            if (!byName[s.product]) byName[s.product] = { ...s, times: [s.time] };
            else byName[s.product].times.push(s.time);
        });
        const products = Object.values(byName);
        box.innerHTML = `
            <h3 style="font-size:14px; margin:20px 0 4px;"><i class="fa-solid fa-wand-magic-sparkles"></i> Recommended Dr Tanya products for you</h3>
            <div class="product-rec-grid">
                ${products.map(p => `
                    <div class="product-rec-card">
                        <div class="prc-head">
                            <span class="prc-name">${esc(p.product)}</span>
                            ${p.in_stock === false ? '<span class="sold-out-pill">Sold out</span>' : ''}
                        </div>
                        <div class="prc-time">${p.times.join(' + ')}</div>
                        <p class="prc-details">${esc(p.details)}</p>
                        <div class="prc-footer">
                            ${p.price_inr != null ? `<span class="step-price">₹${p.price_inr}${p.list_price_inr > p.price_inr ? ` <s>₹${p.list_price_inr}</s>` : ''}</span>` : ''}
                            ${p.url ? `<a class="step-shop-link" href="${p.url}" target="_blank" rel="noopener">${p.in_stock === false ? 'View product' : 'Shop now'} <i class="fa-solid fa-arrow-up-right-from-square"></i></a>` : ''}
                        </div>
                    </div>
                `).join('')}
            </div>
            ${routine.notes && routine.notes.length ? `
                <div class="routine-notes" style="display:block; margin-top:16px;">
                    <div class="routine-notes-head"><i class="fa-solid fa-circle-info"></i> Good to know</div>
                    <ul>${routine.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>
                </div>` : ''}`;
    }

    document.getElementById('start-consult-btn').addEventListener('click', () => {
        api('/api/consultations', { method: 'POST', body: JSON.stringify({ scan_id: state.scanId }) })
            .then(con => {
                state.consultationId = con.id;
                showStep('payment');
            }).catch(err => alert('Could not start consultation: ' + err.message));
    });

    // ---------------------------------------------------------------- step 3: choose a time + demo payment
    // The patient picks an appointment slot first; "Confirm" reserves it and marks the (demo) payment in one request,
    // so a slot can never be held without a paid consultation, nor a payment taken for a slot that was just lost.
    const payBtn = document.getElementById('confirm-pay-btn');
    const sumBox = document.getElementById('appt-summary');
    let payScheduler = null;
    function paintSummary(slot) {
        if (!sumBox) return;
        sumBox.innerHTML = `
            <h3>Appointment summary</h3>
            <div class="kv"><span>Doctor</span><span>Dermatologist (assigned when you confirm)</span></div>
            <div class="kv"><span>Date</span><span>${slot ? esc(slot.day_label) : '<em class="muted">Choose a time</em>'}</span></div>
            <div class="kv"><span>Time</span><span>${slot ? esc(slot.label + ' · ' + slot.minutes + ' min ' + slot.tz_label) : '<em class="muted">—</em>'}</span></div>
            <div class="kv"><span>Includes</span><span>Questionnaire + 5-angle scan + AI report</span></div>
            <div class="kv"><span>Fee</span><span>$99.00 (demo)</span></div>
            <p class="muted" style="margin-top:12px; line-height:1.5;">You can reschedule or cancel up to 2 hours before. Your doctor reviews your case, and no product is prescribed until they approve it.</p>`;
    }
    function ensurePayScheduler() {
        if (!payScheduler && window.ClarityScheduler) {
            payScheduler = window.ClarityScheduler.create(document.getElementById('scheduler'), {
                onSelect: slot => { payBtn.disabled = !slot; paintSummary(slot); const e = document.getElementById('pay-error'); if (e) e.textContent = ''; },
            });
        }
        return payScheduler;
    }
    function openPayment() { payBtn.disabled = !(payScheduler && payScheduler.selected()); paintSummary(payScheduler && payScheduler.selected()); const sc = ensurePayScheduler(); sc && sc.load(); }

    payBtn.addEventListener('click', () => {
        const slot = payScheduler && payScheduler.selected();
        const err = document.getElementById('pay-error');
        if (!slot) { if (err) err.textContent = 'Please choose an appointment time first.'; return; }
        payBtn.disabled = true; if (err) err.textContent = '';
        api(`/api/consultations/${state.consultationId}/pay`, { method: 'POST', body: JSON.stringify({ slot_start: slot.start }) })
            .then(data => {
                showStep('pending');
                apptSig = '';
                if (data && data.appointment) {
                    renderAppointment(data);
                }
                pollConsultation();
            })
            .catch(e => {
                payBtn.disabled = false;
                if (err) err.textContent = e.message;
                if (payScheduler) { payScheduler.clear(); payScheduler.reload(); }    // refresh: the slot was probably just taken
            });
    });

    // ---------------------------------------------------------------- step 4: pending (appointment card + doctor review)
    const apptBox = document.getElementById('appt-card');
    let reschedScheduler = null, apptSig = '', reschedOpen = false;
    function renderAppointment(con) {
        if (!apptBox || reschedOpen) return;
        const a = con.appointment, sig = a ? [a.id, a.status, a.start, a.can_change].join('|') : 'none';
        if (sig === apptSig) return;
        apptSig = sig;
        if (!a || a.status !== 'booked') {
            apptBox.innerHTML = `<div class="panel tinted"><h3>Your appointment</h3>
                <p class="muted" style="margin-bottom:12px;">${a && a.status === 'cancelled' ? 'Your appointment was cancelled.' : 'No appointment time is booked yet.'} Your doctor will still review your case — booking a time lets them plan your consultation.</p>
                <button type="button" class="btn btn-primary" id="appt-book">Book a time</button></div>`;
            document.getElementById('appt-book').onclick = () => openResched(con, 'Book a time');
            return;
        }
        apptBox.innerHTML = `
            <div class="panel tinted" style="border-left: 4px solid var(--accent-cyan, #00d2ff); background: rgba(0, 210, 255, 0.04);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:8px;">
                    <h3 style="margin:0; color:var(--accent-cyan, #00d2ff);"><i class="fa-solid fa-circle-check"></i> Appointment Confirmed</h3>
                    <span class="pill ok" style="font-weight:600;"><i class="fa-solid fa-calendar-check"></i> Booked</span>
                </div>
                <div class="kv"><span>Date</span><strong>${esc(a.day_label)}</strong></div>
                <div class="kv"><span>Time</span><strong>${esc(a.time_label)}</strong></div>
                <div class="kv"><span>Doctor</span><strong>${esc(a.doctor_name || 'Dr. Tanya Unni')}</strong></div>
                <div class="kv"><span>Consultation Fee</span><span class="pill">$99.00 (Paid)</span></div>
                <div style="display:flex; gap:10px; margin-top:14px; flex-wrap:wrap;">
                    <button type="button" class="btn btn-secondary btn-small" id="appt-resched" ${a.can_change ? '' : 'disabled'}><i class="fa-solid fa-clock-rotate-left"></i> Reschedule</button>
                    <button type="button" class="btn btn-secondary btn-small" id="appt-cancel" ${a.can_change ? '' : 'disabled'}><i class="fa-solid fa-xmark"></i> Cancel appointment</button>
                </div>
                ${a.can_change ? '' : '<p class="muted" style="margin-top:10px;">Changes are closed within 2 hours of the start. Please contact the clinic.</p>'}
                <div id="appt-err" class="form-error" style="margin-top:8px;"></div>
            </div>`;
        document.getElementById('appt-resched').onclick = () => openResched(con, 'Reschedule');
        document.getElementById('appt-cancel').onclick = () => {
            if (!confirm('Cancel this appointment? You can book another time afterwards.')) return;
            api(`/api/appointments/${a.id}/cancel`, { method: 'POST' })
                .then(() => { apptSig = ''; pollConsultation(); })
                .catch(e => { const x = document.getElementById('appt-err'); if (x) x.textContent = e.message; });
        };
    }
    function openResched(con, title) {
        reschedOpen = true;
        apptBox.innerHTML = `<div class="panel"><div class="sched-modal-head"><h3>${esc(title)}</h3><button type="button" class="link-btn" id="resched-close">Close</button></div>
            <div id="resched-root"></div>
            <div id="resched-err" class="form-error"></div>
            <button type="button" class="btn btn-primary" id="resched-confirm" disabled style="margin-top:12px;">Confirm this time</button></div>`;
        const confirmBtn = document.getElementById('resched-confirm');
        reschedScheduler = window.ClarityScheduler.create(document.getElementById('resched-root'), { onSelect: slot => { confirmBtn.disabled = !slot; } });
        reschedScheduler.load();
        const close = () => { reschedOpen = false; apptSig = ''; renderAppointment(con); };
        document.getElementById('resched-close').onclick = close;
        confirmBtn.onclick = () => {
            const slot = reschedScheduler.selected(); if (!slot) return;
            confirmBtn.disabled = true;
            api(`/api/consultations/${state.consultationId}/appointment`, { method: 'POST', body: JSON.stringify({ slot_start: slot.start }) })
                .then(() => { reschedOpen = false; apptSig = ''; pollConsultation(); })
                .catch(e => { document.getElementById('resched-err').textContent = e.message; reschedScheduler.clear(); reschedScheduler.reload(); });
        };
    }

    function pollConsultation() {
        clearTimeout(state.pollTimer);
        api(`/api/consultations/${state.consultationId}`).then(con => {
            if (con.status === 'completed') { renderPlan(con); showStep('completed'); return; }
            renderAppointment(con);
            state.pollTimer = setTimeout(pollConsultation, 4000);
        }).catch(() => { state.pollTimer = setTimeout(pollConsultation, 6000); });
    }

    // ---------------------------------------------------------------- step 5: plan + follow-up
    function renderPlan(con) {
        let routineHtml = '';
        try {
            const r = (con.scan && con.scan.routine) || (con.scan && con.scan.routine_json ? (typeof con.scan.routine_json === 'string' ? JSON.parse(con.scan.routine_json) : con.scan.routine_json) : null);
            if (r) {
                const timed = [
                    ...(r.am_routine || []).map(s => ({ ...s, time: '☀️ AM' })),
                    ...(r.pm_routine || []).map(s => ({ ...s, time: '🌙 PM' })),
                    ...(r.weekly_routine || []).map(s => ({ ...s, time: '✨ Weekly' })),
                ];
                if (timed.length) {
                    routineHtml = `
                        <div style="margin-top:20px; padding:16px; background:rgba(0, 210, 255, 0.04); border:1px solid rgba(0, 210, 255, 0.2); border-radius:10px;">
                            <h4 style="margin:0 0 10px; font-size:14px; color:var(--accent-cyan, #00d2ff);"><i class="fa-solid fa-prescription-bottle-medical"></i> Doctor-Prescribed Daily Skincare Regimen (Dr. Tanya Products)</h4>
                            <div style="display:grid; gap:10px;">
                                ${timed.map(p => `
                                    <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.02); padding:8px 12px; border-radius:6px; border:1px solid rgba(255,255,255,0.06);">
                                        <div><span class="tag" style="margin-right:8px;">${esc(p.time)}</span><strong>${esc(p.product || p.step || '')}</strong><div class="muted" style="font-size:12px; margin-top:2px;">${esc(p.details || '')}</div></div>
                                        ${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener" class="btn btn-secondary btn-small" style="font-size:11px; padding:4px 8px;">View <i class="fa-solid fa-arrow-up-right-from-square"></i></a>` : ''}
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    `;
                }
            }
        } catch (e) { console.error('Routine render error:', e); }

        document.getElementById('plan-content').innerHTML = `
            <div class="note-box" style="margin-top:0;"><strong>Doctor-approved plan.</strong> Follow only the instructions issued by your consulting doctor.</div>
            <div class="plan-field" style="margin-top:18px;"><div class="pf-label">Symptoms</div><div class="pf-value">${esc(con.symptoms || '—')}</div></div>
            <div class="plan-field"><div class="pf-label">Diagnosis</div><div class="pf-value">${esc(con.diagnosis || '—')}</div></div>
            <div class="plan-field"><div class="pf-label">Cure / treatment plan</div><div class="pf-value">${esc(con.cure || '—')}</div></div>
            <div class="plan-field"><div class="pf-label">Prescription</div><div class="pf-value">${esc(con.prescription || '—')}</div></div>
            <div class="plan-field"><div class="pf-label">Previous condition / records</div><div class="pf-value">${esc(con.previous_condition || '—')}</div></div>
            ${routineHtml}
            <div style="display:flex; justify-content:space-between; align-items:center; margin-top:20px; flex-wrap:wrap; gap:10px;">
                <button type="button" class="btn btn-secondary btn-small" onclick="window.print()"><i class="fa-solid fa-print"></i> Print / Save Report (PDF)</button>
                <span class="muted" style="font-size:12px;">Consultation ID: ${esc(con.id)}</span>
            </div>`;
        const shortDate = iso => iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '—';
        const side = document.getElementById('plan-side');
        if (side) side.innerHTML = `
            <h3>Your care timeline</h3>
            <div style="margin-bottom:12px;"><span class="tag">Reviewed by ${esc(con.doctor_name || 'your doctor')}</span></div>
            <ul class="timeline">
                ${con.appointment && con.appointment.status !== 'cancelled' ? `<li><span class="tl-date">${esc(shortDate(con.appointment.start))}</span><div><div class="tl-title">Consultation appointment</div><div class="tl-sub">${esc(con.appointment.time_label)} with ${esc(con.appointment.doctor_name || 'your doctor')}.</div></div></li>` : ''}
                <li><span class="tl-date">${esc(shortDate(con.completed_at))}</span><div><div class="tl-title">Scan + doctor consultation</div><div class="tl-sub">Your scan was reviewed and this plan approved.</div></div></li>
                <li><span class="tl-date">${esc(((con.checkpoints || [])[0] || { opens_label: '' }).opens_label.split(' ').slice(1).join(' '))}</span><div><div class="tl-title">Check-in scans · Day 15, 30 &amp; 45</div><div class="tl-sub">The first opens 15 days after your consultation. See below.</div></div></li>
            </ul>
            `;
        renderCheckpoints(con.checkpoints || [], !!(state.journey && state.journey.can_start_new));
    }

    // Day 15 / 30 / 45 check-in scans: only on their own calendar days, so the comparison shows real change from the
    // doctor's prescription, products and medicines.
    function renderCheckpoints(cps, canStartNew) {
        const chip = c => c.status === 'done' ? '<span class="pill ok">Done</span>' : c.status === 'available' ? '<span class="pill warn">Open now</span>'
            : c.status === 'missed' ? '<span class="pill danger">Missed</span>' : `<span class="pill">Opens ${esc(c.opens_label)}</span>`;
        const open = cps.find(c => c.status === 'available');
        document.getElementById('followup-note').innerHTML = `
            <ul class="timeline cp" style="margin:0 0 10px;">${cps.map(c => `<li><span class="tl-date">${esc(c.label)}</span>
                <div><div class="tl-title">${esc(c.status === 'done' ? 'Check-in scan completed' : c.status === 'available' ? 'Your check-in scan is open' : c.status === 'missed' ? 'Window passed' : 'Not open yet')}</div>
                <div class="tl-sub">${esc(c.opens_label)}${c.status === 'available' ? ' – ' + esc(c.closes_label) : ''}</div></div>${chip(c)}</li>`).join('')}</ul>
            <p class="muted" style="line-height:1.5;">Scans are only available on these check-in days, counted from the day your doctor completed your consultation, so we can show the real difference your prescription, products and medicines make.</p>`;
        const btn = document.getElementById('followup-scan-btn');
        if (open) { btn.style.display = 'block'; btn.disabled = false; btn.dataset.day = open.day; btn.innerHTML = `<i class="fa-solid fa-camera"></i> Start my Day ${open.day} scan`; }
        else if (canStartNew) { btn.style.display = 'block'; btn.disabled = false; btn.dataset.day = ''; btn.innerHTML = '<i class="fa-solid fa-rotate-right"></i> Start a new assessment'; }
        else { btn.style.display = 'none'; }
        renderComparison(cps);
    }

    document.getElementById('followup-scan-btn').addEventListener('click', e => {
        const day = e.currentTarget.dataset.day;
        state.followupMode = !!day; state.followupDay = day ? Number(day) : null;
        resetCameraUI();
        const h = document.getElementById('scan-title'), eb = document.getElementById('scan-eyebrow');
        if (h) h.textContent = day ? `Your Day ${day} check-in scan.` : 'Your facial scan, guided.';
        if (eb) eb.textContent = day ? `Check-in · Day ${day}` : 'Clarity journey / Step 1 of 5';
        showStep('scan');
    });

    function renderComparison(cps) {
        const box = document.getElementById('comparison-content');
        const done = (cps || []).filter(c => c.status === 'done' && c.comparison);
        if (!done.length) { box.innerHTML = ''; return; }
        const deltaClass = d => d > 0.05 ? 'up' : d < -0.05 ? 'down' : 'flat';
        const arrow = d => d > 0.05 ? '▲' : d < -0.05 ? '▼' : '—';
        box.innerHTML = `<h3 style="font-size:20px; margin:26px 0 4px;">Your progress</h3>
            <p class="muted" style="margin-bottom:6px;">Each check-in compared with your first scan. Lower severity and a higher score both mean improvement.</p>` +
            done.map(c => {
                const cmp = c.comparison, rows = Object.entries(cmp.per_attribute || {}).filter(([, v]) => Math.abs(v.delta) > 0.05);
                return `<h4 style="margin:18px 0 6px; font-size:15px;">${esc(c.label)} vs first scan</h4>
                <div class="compare-grid">
                    <div class="compare-card"><div>Overall health score</div>
                        <div>${cmp.score_before ?? '—'} → ${cmp.score_after ?? '—'}
                        <span class="cmp-delta ${deltaClass(cmp.score_delta || 0)}">${arrow(cmp.score_delta || 0)} ${cmp.score_delta != null ? Math.abs(cmp.score_delta) : ''}</span></div></div>
                    ${rows.map(([a, v]) => `<div class="compare-card"><div>${esc(nice(a))}</div>
                        <div>${v.before.toFixed(1)} → ${v.after.toFixed(1)}
                        <span class="cmp-delta ${deltaClass(v.delta)}">${arrow(v.delta)} ${Math.abs(v.delta).toFixed(1)}</span></div></div>`).join('')}
                </div>`;
            }).join('');
    }

    // ---------------------------------------------------------------- the server decides which step the patient is on
    async function syncJourney() {
        let j;
        try { j = await api('/api/me/journey'); } catch (e) { showStep('scan'); return; }
        state.journey = j; state.consultationId = j.consultation_id || null;
        clearTimeout(state.pollTimer);
        if (j.stage === 'report') { state.scanId = j.report.scan_id; renderReport(j.report); showStep('report'); }
        else if (j.stage === 'payment') { showStep('payment'); }
        else if (j.stage === 'pending') { showStep('pending'); apptSig = ''; pollConsultation(); }
        else if (j.stage === 'completed') {
            api(`/api/consultations/${state.consultationId}`).then(con => { renderPlan(con); showStep('completed'); }).catch(() => showStep('scan'));
        } else { state.followupMode = false; showStep('scan'); }
    }

    // ---------------------------------------------------------------- auth gate + resume in-progress session
    (async function init() {
        const user = await window.requireAuth('patient');
        if (!user) return; // requireAuth already redirected to /login
        state.patientId = user.patient_id;
        state.patientName = user.full_name;
        const bar = document.getElementById('session-bar');
        if (bar) bar.innerHTML = `${esc(user.full_name)} · ID ${esc(user.mrn || user.user_code || '')} · <a href="#" id="logout-link">Sign out</a>`;
        document.getElementById('logout-link')?.addEventListener('click', e => { e.preventDefault(); window.clarityLogout(); });

        if (!window.isSecureContext && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
            const errEl = document.getElementById('cam-error');
            if (errEl) {
                errEl.innerHTML = `
                    <div style="background: rgba(0, 210, 255, 0.08); border: 1px solid rgba(0, 210, 255, 0.25); border-radius: 8px; padding: 12px; margin-top: 10px; font-size: 13px; line-height: 1.4; text-align: left;">
                        <span style="color: var(--accent-cyan, #00d2ff); font-weight: 600;">💡 Testing on this Mac?</span>
                        Browsers block camera access over plain HTTP on IP addresses (<code>${location.hostname}</code>).
                        To use your webcam directly, open <a href="http://localhost:3001/patient" style="color: var(--accent-cyan, #00d2ff); text-decoration: underline; font-weight: 600;">http://localhost:3001/patient</a> (where camera is fully enabled).
                    </div>
                `;
            }
        }

        await syncJourney();
    })();
})();
