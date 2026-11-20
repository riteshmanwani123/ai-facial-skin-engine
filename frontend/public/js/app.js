function __clarityAppInit() {
    const navButtons = document.querySelectorAll('.nav-btn');
    const tabPanes = document.querySelectorAll('.tab-pane');
    
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const uploadPlaceholder = document.getElementById('upload-placeholder');
    const previewContainer = document.getElementById('preview-container');
    const imagePreview = document.getElementById('image-preview');
    const reuploadBtn = document.getElementById('reupload-btn');
    const analyzeBtn = document.getElementById('analyze-btn');
    const sampleChips = document.querySelectorAll('.sample-chip');

    const emptyState = document.getElementById('empty-state');
    const loadingState = document.getElementById('loading-state');
    const resultsContent = document.getElementById('results-content');

    let selectedImageBase64 = null;
    let currentAnalysis = null;
    let selectedDoctorId = null;

    navButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetTab = btn.getAttribute('data-tab');
            navButtons.forEach(b => b.classList.remove('active'));
            tabPanes.forEach(p => p.style.display = 'none');

            btn.classList.add('active');
            const activePane = document.getElementById(targetTab);
            if (activePane) activePane.style.display = 'block';

            if (targetTab === 'metrics-tab') {
                loadHospitalMetrics();
            } else if (targetTab === 'doctor-tab') {
                loadDoctorNetwork();
            }
        });
    });

    dropZone.addEventListener('click', (e) => {
        if (e.target !== reuploadBtn && !reuploadBtn.contains(e.target)) {
            fileInput.click();
        }
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files && e.target.files[0]) {
            handleFileSelect(e.target.files[0]);
        }
    });

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = 'var(--accent-cyan)';
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.style.borderColor = 'rgba(255, 255, 255, 0.15)';
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = 'rgba(255, 255, 255, 0.15)';
        if (e.dataTransfer.files && e.dataTransfer.files[0]) {
            handleFileSelect(e.dataTransfer.files[0]);
        }
    });

    reuploadBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        fileInput.value = '';
        selectedImageBase64 = null;
        uploadPlaceholder.style.display = 'block';
        previewContainer.style.display = 'none';
        analyzeBtn.disabled = true;
    });

    function handleFileSelect(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            selectedImageBase64 = e.target.result;
            imagePreview.src = selectedImageBase64;
            uploadPlaceholder.style.display = 'none';
            previewContainer.style.display = 'flex';
            analyzeBtn.disabled = false;
        };
        reader.readAsDataURL(file);
    }

    sampleChips.forEach(chip => {
        chip.addEventListener('click', (e) => {
            e.stopPropagation();
            const sampleType = chip.getAttribute('data-sample');
            loadPresetSample(sampleType);
        });
    });

    function loadPresetSample(sampleType) {
        const canvas = document.createElement('canvas');
        canvas.width = 400;
        canvas.height = 400;
        const ctx = canvas.getContext('2d');
        
        if (sampleType === 'melasma') {
            ctx.fillStyle = '#8a5038';
            ctx.fillRect(0, 0, 400, 400);
            ctx.fillStyle = '#4a2518';
            ctx.beginPath(); ctx.arc(150, 180, 70, 0, Math.PI * 2); ctx.fill();
            ctx.beginPath(); ctx.arc(250, 180, 70, 0, Math.PI * 2); ctx.fill();
            ctx.fillStyle = '#ffffff';
            ctx.font = 'bold 16px Inter';
            ctx.fillText('Sample: Bilateral Melasma Target', 40, 360);
        } else if (sampleType === 'tanning') {
            ctx.fillStyle = '#9e623b';
            ctx.fillRect(0, 0, 400, 400);
            ctx.fillStyle = '#ffffff';
            ctx.font = 'bold 16px Inter';
            ctx.fillText('Sample: UV Sun Tanning Target', 40, 360);
        } else {
            ctx.fillStyle = sampleType === 'dry' ? '#d6a090' : (sampleType === 'oily' ? '#c89578' : '#d2a688');
            ctx.fillRect(0, 0, 400, 400);
            ctx.fillStyle = '#ffffff';
            ctx.font = 'bold 16px Inter';
            ctx.fillText(`Sample: ${sampleType.toUpperCase()}`, 40, 360);
        }

        selectedImageBase64 = canvas.toDataURL('image/jpeg');
        imagePreview.src = selectedImageBase64;
        uploadPlaceholder.style.display = 'none';
        previewContainer.style.display = 'flex';
        analyzeBtn.disabled = false;

        runSkinAnalysis();
    }

    analyzeBtn.addEventListener('click', () => {
        runSkinAnalysis();
    });

    function runSkinAnalysis() {
        if (!selectedImageBase64) return;

        emptyState.style.display = 'none';
        resultsContent.style.display = 'none';
        loadingState.style.display = 'block';

        fetch('/api/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_base64: selectedImageBase64, patient_id: (window.getSelectedPatientId && window.getSelectedPatientId()) || null })
        })
        .then(res => res.json())
        .then(data => {
            loadingState.style.display = 'none';
            if (data.success) {
                currentAnalysis = data;
                renderResults(data);
            } else {
                alert('Analysis error: ' + (data.error || 'Unknown error'));
                emptyState.style.display = 'block';
            }
        })
        .catch(err => {
            loadingState.style.display = 'none';
            emptyState.style.display = 'block';
            console.error('Error analyzing skin:', err);
        });
    }

    function renderResults(data) {
        resultsContent.style.display = 'block';

        // 1. Triage Banner (RED vs GREEN)
        const triageBanner = document.getElementById('triage-banner');
        const triageBadge = document.getElementById('triage-badge');

        if (data.triage.banner_color === 'red') {
            triageBanner.className = 'triage-banner severe';
            triageBanner.innerHTML = `
                <div class="triage-icon"><i class="fa-solid fa-triangle-exclamation"></i></div>
                <div class="triage-info">
                    <h4>${data.triage.banner_title}</h4>
                    <p>${data.triage.banner_desc}</p>
                    <button class="btn btn-danger" style="margin-top:10px; font-size:12px; padding:6px 14px;" onclick="switchToDoctorTab()">
                        <i class="fa-solid fa-user-doctor"></i> Connect with Dermatologist Now
                    </button>
                </div>
            `;
            triageBadge.style.display = 'inline-block';
        } else {
            triageBanner.className = 'triage-banner safe';
            triageBanner.innerHTML = `
                <div class="triage-icon"><i class="fa-solid fa-circle-check"></i></div>
                <div class="triage-info">
                    <h4>${data.triage.banner_title}</h4>
                    <p>${data.triage.banner_desc}</p>
                    <button class="btn btn-primary" style="margin-top:10px; font-size:12px; padding:6px 14px; width:auto;" onclick="switchToRoutineTab()">
                        <i class="fa-solid fa-prescription-bottle-medical"></i> View Hyperpigmentation Skincare Routine
                    </button>
                </div>
            `;
            triageBadge.style.display = 'none';
        }

        // 2. CV Badges (Skin Tone & Image Quality)
        const cvBadgesRow = document.getElementById('cv-badges-row');
        cvBadgesRow.innerHTML = '';
        if (data.cv_metrics) {
            const tone = data.cv_metrics.fitzpatrick_skin_tone;
            const quality = data.cv_metrics.image_quality;
            cvBadgesRow.innerHTML = `
                <span class="status-indicator" style="color:#00d2ff; background:rgba(0,210,255,0.1); border-color:rgba(0,210,255,0.3);">
                    <i class="fa-solid fa-palette"></i> Skin Tone: ${tone} (ITA: ${data.cv_metrics.ita_degrees}°)
                </span>
                <span class="status-indicator" style="color:#10b981; background:rgba(16,185,129,0.1); border-color:rgba(16,185,129,0.3);">
                    <i class="fa-solid fa-camera-rotate"></i> Quality: ${quality.focus_rating} (${quality.resolution})
                </span>
            `;
        }

        // 3. Health Score Gauge
        const scoreVal = document.getElementById('health-score-val');
        const circle = document.getElementById('health-circle');
        scoreVal.textContent = `${data.overall_health_score}%`;
        circle.setAttribute('stroke-dasharray', `${data.overall_health_score}, 100`);

        // 4. Skin Type
        document.getElementById('detected-skin-type').textContent = data.skin_type.toUpperCase();
        document.getElementById('skin-type-conf').textContent = `Confidence: ${(data.skin_type_confidence * 100).toFixed(1)}%`;

        const probBars = document.getElementById('prob-bars');
        probBars.innerHTML = '';
        Object.entries(data.skin_type_probabilities).forEach(([st, prob]) => {
            const pct = (prob * 100).toFixed(0);
            probBars.innerHTML += `
                <div class="prob-item">
                    <span class="prob-label">${st.toUpperCase()}</span>
                    <div class="prob-track"><div class="prob-fill" style="width:${pct}%"></div></div>
                    <span style="font-size:10px; color:#94a3b8; width:28px;">${pct}%</span>
                </div>
            `;
        });

        // 5. 11 Attribute Severity Grid
        const sevGrid = document.getElementById('severity-grid');
        sevGrid.innerHTML = '';
        Object.entries(data.severity_scores).forEach(([attr, val]) => {
            const pct = (val / 5.0 * 100).toFixed(0);
            const formattedName = attr.replaceAll('_', ' ');
            const isHighlight = attr === 'pigmentation_melasma' || attr === 'tanning_severity';
            sevGrid.innerHTML += `
                <div class="sev-card" style="${isHighlight ? 'border-color: rgba(0, 210, 255, 0.4); background: rgba(0, 210, 255, 0.05);' : ''}">
                    <div class="sev-header">
                        <span class="sev-title">${formattedName} ${isHighlight ? '★' : ''}</span>
                        <span class="sev-score" style="${isHighlight ? 'color:#00d2ff;' : ''}">${val.toFixed(1)} / 5.0</span>
                    </div>
                    <div class="sev-bar-track">
                        <div class="sev-bar-fill" style="width: ${pct}%"></div>
                    </div>
                </div>
            `;
        });

        // 6. Render Routine
        renderRoutine(data.routine);

        // 7. Clinician verification panel (clinic.js)
        if (window.onAnalysisComplete) window.onAnalysisComplete(data);
    }

    window.switchToDoctorTab = function() {
        document.querySelector('[data-tab="doctor-tab"]').click();
    };

    window.switchToRoutineTab = function() {
        document.querySelector('[data-tab="routine-tab"]').click();
    };

    function stepCardHtml(s) {
        const priceHtml = s.price_inr != null
            ? `<span class="step-price">₹${s.price_inr}${s.list_price_inr && s.list_price_inr > s.price_inr ? ` <s>₹${s.list_price_inr}</s>` : ''}</span>` : '';
        const shopBtn = s.url
            ? `<a class="step-shop-link" href="${s.url}" target="_blank" rel="noopener">${s.in_stock === false ? 'View product' : 'Shop now'} <i class="fa-solid fa-arrow-up-right-from-square"></i></a>` : '';
        return `
            <div class="step-card">
                <div class="step-number">${s.step}</div>
                <div class="step-product-name">${s.product}</div>
                <div class="step-details">${s.details}</div>
                <div class="step-usage">${s.usage || ''}</div>
                <div class="step-footer">${priceHtml}${shopBtn}</div>
            </div>`;
    }

    function renderRoutine(routine) {
        const amContainer = document.getElementById('am-steps');
        const pmContainer = document.getElementById('pm-steps');
        const weeklyContainer = document.getElementById('weekly-steps');
        const weeklyColumn = document.getElementById('weekly-column');
        const ingContainer = document.getElementById('ingredient-tags');
        const notesContainer = document.getElementById('routine-notes');

        amContainer.innerHTML = routine.am_routine.map(stepCardHtml).join('');
        pmContainer.innerHTML = routine.pm_routine.map(stepCardHtml).join('');

        if (routine.weekly_routine && routine.weekly_routine.length) {
            weeklyColumn.style.display = '';
            weeklyContainer.innerHTML = routine.weekly_routine.map(stepCardHtml).join('');
        } else if (weeklyColumn) {
            weeklyColumn.style.display = 'none';
        }

        ingContainer.innerHTML = routine.key_ingredients.map(ing => `
            <span class="ing-tag"><i class="fa-solid fa-leaf"></i> ${ing}</span>
        `).join('');

        if (notesContainer) {
            notesContainer.innerHTML = (routine.notes && routine.notes.length)
                ? `<div class="routine-notes-head"><i class="fa-solid fa-circle-info"></i> Good to know</div>
                   <ul>${routine.notes.map(n => `<li>${n}</li>`).join('')}</ul>` : '';
            notesContainer.style.display = (routine.notes && routine.notes.length) ? 'block' : 'none';
        }
    }

    function loadDoctorNetwork() {
        fetch('/api/doctors')
        .then(res => res.json())
        .then(data => {
            const grid = document.getElementById('doctors-grid');
            grid.innerHTML = '';
            
            data.doctors.forEach(doc => {
                grid.innerHTML += `
                    <div class="doctor-card-item">
                        <div class="doc-avatar-row">
                            <img src="${doc.avatar}" class="doc-avatar" alt="${doc.name}">
                            <div>
                                <div class="doc-name">${doc.name}</div>
                                <div class="doc-title">${doc.title}</div>
                                <div class="doc-hospital">${doc.hospital}</div>
                            </div>
                        </div>
                        <div class="doc-specs">
                            ${doc.specialties.map(s => `<span class="spec-chip">${s}</span>`).join('')}
                        </div>
                        <button class="btn btn-primary" onclick="openBookingModal('${doc.id}', '${doc.name}', '${doc.hospital}')">
                            <i class="fa-solid fa-calendar-plus"></i> Book Tele-Consultation
                        </button>
                    </div>
                `;
            });
        });
    }

    const modal = document.getElementById('booking-modal');
    const closeModalBtn = document.getElementById('close-modal-btn');
    const bookingForm = document.getElementById('booking-form');

    window.openBookingModal = function(docId, docName, hospital) {
        selectedDoctorId = docId;
        document.getElementById('doc-summary-box').innerHTML = `
            <strong>Doctor:</strong> ${docName}<br>
            <strong>Facility:</strong> ${hospital}<br>
            <strong>Status:</strong> Available for Instant Tele-Dermatology
        `;
        modal.style.display = 'flex';
    };

    closeModalBtn.addEventListener('click', () => { modal.style.display = 'none'; });

    bookingForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const patientName = document.getElementById('patient-name').value;

        fetch('/api/book-consultation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ doctor_id: selectedDoctorId, patient_name: patientName })
        })
        .then(res => res.json())
        .then(res => {
            document.getElementById('booking-confirmation').style.display = 'block';
            document.getElementById('booking-confirmation').innerHTML = `
                <div style="color:var(--accent-emerald); font-weight:700; margin-bottom:6px;"><i class="fa-solid fa-circle-check"></i> Appointment Confirmed!</div>
                <p>Booking Ref: <strong>${res.booking_id}</strong></p>
                <p>Scheduled: ${res.appointment_time}</p>
                <p style="font-size:11px; color:#94a3b8; margin-top:6px;">${res.clinical_summary}</p>
            `;
        });
    });

    function loadHospitalMetrics() {
        fetch('/api/hospital-metrics')
        .then(res => res.json())
        .then(data => {
            const perf = data.clinical_performance_metrics || {};
            const pct = v => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
            document.getElementById('stat-accuracy').textContent = pct(perf.accuracy);
            document.getElementById('stat-f1').textContent = perf.macro_f1_score != null ? perf.macro_f1_score.toFixed(3) : '—';
            document.getElementById('stat-sensitivity').textContent = pct(perf.balanced_accuracy);
            document.getElementById('stat-params').textContent = perf.severity_mae != null ? perf.severity_mae.toFixed(2) : '—';
            if (Array.isArray(data.notes)) {
                const ul = document.getElementById('eval-notes');
                if (ul) ul.innerHTML = data.notes.map(n => `<li>${String(n).replace(/</g, '&lt;')}</li>`).join('');
            }

            const cmData = perf.confusion_matrix;
            if (cmData) {
                const container = document.getElementById('cm-table-container');
                let html = `<table><thead><tr><th>True \\ Predicted</th>`;
                cmData.labels.forEach(lbl => { html += `<th>${lbl.toUpperCase()}</th>`; });
                html += `</tr></thead><tbody>`;

                cmData.matrix.forEach((row, i) => {
                    html += `<tr><th>${cmData.labels[i].toUpperCase()}</th>`;
                    row.forEach((cellVal, j) => {
                        const isDiag = i === j;
                        html += `<td class="${isDiag ? 'cm-diag' : ''}">${cellVal}</td>`;
                    });
                    html += `</tr>`;
                });
                html += `</tbody></table>`;
                container.innerHTML = html;
            }
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', __clarityAppInit);
} else {
    __clarityAppInit();
}
