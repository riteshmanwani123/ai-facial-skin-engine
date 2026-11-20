"use client";

import Script from "next/script";

export default function ClinicDashboard() {
  return (
    <>
      <style>{"body{visibility:hidden}"}</style>
      <link rel="stylesheet" href="/css/style.css" />
      <link rel="stylesheet" href="/css/clinic.css" />
      <link rel="stylesheet" href="/css/theme.css?v=20261014" />

      <header className="app-header">
        <div className="header-container">
          <div className="brand">
            <img src="/images/clarity_logo.jpg" alt="CLARITY by Dr. Tanya" className="brand-logo-img" />
            <div className="brand-text">
              <span className="brand-name">CLARITY <span className="highlight">by Dr. Tanya</span></span>
              <span className="brand-tag">SKIN HEALTH CONFIDENCE • DINOv2 CLINICAL ENGINE</span>
            </div>
          </div>

          <nav className="nav-tabs">
            <button className="nav-btn active" data-tab="scan-tab"><i className="fa-solid fa-camera"></i> AI Facial Scan</button>
            <button className="nav-btn" data-tab="routine-tab"><i className="fa-solid fa-prescription-bottle-medical"></i> Skincare Routine</button>
            <button className="nav-btn" data-tab="doctor-tab" id="doctor-nav-btn"><i className="fa-solid fa-user-doctor"></i> Doctor Referral <span className="badge alert-badge" id="triage-badge" style={{ display: "none" }}>ALERT</span></button>
            <button className="nav-btn" data-tab="patients-tab"><i className="fa-solid fa-hospital-user"></i> Patients</button>
            <button className="nav-btn" data-tab="metrics-tab"><i className="fa-solid fa-chart-line"></i> Hospital Deliverables</button>
          </nav>

          <div className="header-actions">
            <span className="session-bar" id="session-bar"></span>
            <a className="btn btn-secondary btn-small" href="/admin"><i className="fa-solid fa-user-shield"></i> Admin console</a>
            <span className="status-indicator"><i className="fa-solid fa-circle"></i> Admin · Learning off</span>
          </div>
        </div>
      </header>

      <main className="main-content">
        <section id="scan-tab" className="tab-pane active">
          <div className="scan-grid">
            <div className="card scan-card">
              <div className="card-header">
                <h2><i className="fa-solid fa-face-smile"></i> Facial Image Analysis</h2>
                <p>Upload a high-resolution facial photo or capture via webcam</p>
              </div>

              <div className="scan-area" id="drop-zone">
                <input type="file" id="file-input" accept="image/*" style={{ display: "none" }} />

                <div className="upload-placeholder" id="upload-placeholder">
                  <div className="scan-icon"><i className="fa-solid fa-cloud-arrow-up"></i></div>
                  <h3>Drag &amp; Drop facial image here</h3>
                  <p>or click to select file from device</p>
                  <span className="file-hint">Supported format: JPG, PNG, WEBP (Face clearly visible)</span>
                </div>

                <div className="preview-container" id="preview-container" style={{ display: "none" }}>
                  <img id="image-preview" alt="Facial Scan Target" />
                  <div className="scan-overlay">
                    <div className="face-guide"></div>
                    <div className="scan-line" id="scan-line"></div>
                  </div>
                  <button className="btn btn-secondary change-photo-btn" id="reupload-btn"><i className="fa-solid fa-rotate"></i> Change Photo</button>
                </div>
              </div>

              <div className="patient-bar">
                <label htmlFor="patient-select">Patient for this scan</label>
                <select id="patient-select"><option value="">Anonymous scan (no patient record)</option></select>
                <button className="btn btn-secondary btn-small" data-open-patient-modal=""><i className="fa-solid fa-user-plus"></i> New patient</button>
              </div>

              <div className="action-bar">
                <button className="btn btn-primary analyze-btn" id="analyze-btn" disabled>
                  <i className="fa-solid fa-wand-magic-sparkles"></i> Run DINOv2 Clinical Analysis
                </button>
              </div>

              <div className="preset-samples">
                <span className="preset-label">Or test with clinical sample images:</span>
                <div className="sample-chips">
                  <button className="sample-chip severe" data-sample="melasma"><i className="fa-solid fa-sun"></i> Severe Melasma / Hyperpigmentation</button>
                  <button className="sample-chip" data-sample="tanning"><i className="fa-solid fa-umbrella-beach"></i> Sun Tanning &amp; UV Exposure</button>
                  <button className="sample-chip" data-sample="dry"><i className="fa-solid fa-droplet-slash"></i> Dry Skin</button>
                  <button className="sample-chip" data-sample="oily"><i className="fa-solid fa-oil-well"></i> Oily Skin</button>
                  <button className="sample-chip" data-sample="normal"><i className="fa-solid fa-circle-check"></i> Normal Skin</button>
                </div>
              </div>
            </div>

            <div className="card results-card" id="results-card">
              <div className="empty-state" id="empty-state">
                <i className="fa-solid fa-brain-circuit"></i>
                <h3>Awaiting Facial Input</h3>
                <p>Upload a photo or select a clinical sample to view real-time DINOv2 + CV 11-attribute biometrics, melasma detection, tanning index, and triage safety evaluation.</p>
              </div>

              <div className="loading-state" id="loading-state" style={{ display: "none" }}>
                <div className="spinner"></div>
                <h3>Analyzing Facial Biometrics...</h3>
                <p>DINOv2 + LoRA &amp; 11 Computer Vision models processing Melasma contrast, Tanning, Fitzpatrick Scale, Texture &amp; Quality.</p>
              </div>

              <div className="results-content" id="results-content" style={{ display: "none" }}>
                <div className="triage-banner" id="triage-banner"></div>

                <div className="cv-badges-row" id="cv-badges-row" style={{ marginBottom: 16, display: "flex", gap: 12, flexWrap: "wrap" }}></div>

                <div className="metrics-row">
                  <div className="metric-box skin-health-box">
                    <div className="metric-title">Overall Skin Health Index</div>
                    <div className="radial-gauge">
                      <svg viewBox="0 0 36 36" className="circular-chart">
                        <path className="circle-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
                        <path className="circle" id="health-circle" strokeDasharray="78, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
                        <text x="18" y="20.35" className="percentage" id="health-score-val">78%</text>
                      </svg>
                    </div>
                  </div>

                  <div className="metric-box skin-type-box">
                    <div className="metric-title">Detected Skin Type</div>
                    <div className="skin-type-display">
                      <span className="type-name" id="detected-skin-type">DRY</span>
                      <span className="type-conf" id="skin-type-conf">Confidence: 94.8%</span>
                    </div>
                    <div className="prob-bars" id="prob-bars"></div>
                  </div>
                </div>

                <div className="severity-section">
                  <h3><i className="fa-solid fa-sliders"></i> Dermatological Attribute Sub-Scores</h3>
                  <div className="severity-grid" id="severity-grid"></div>
                </div>

                <div id="verify-panel"></div>
              </div>
            </div>
          </div>
        </section>

        <section id="routine-tab" className="tab-pane" style={{ display: "none" }}>
          <div className="card routine-card">
            <div className="card-header">
              <h2><i className="fa-solid fa-prescription-bottle-medical"></i> Dermatologist-Formulated Routine</h2>
              <p>Tailored AM/PM products matched with active ingredients for Melasma, Tanning &amp; Skin Profile</p>
            </div>

            <div className="routine-content" id="routine-content">
              <div className="routine-columns">
                <div className="routine-column">
                  <div className="routine-header am"><i className="fa-solid fa-sun"></i> Morning (AM) Routine</div>
                  <div className="step-list" id="am-steps"></div>
                </div>

                <div className="routine-column">
                  <div className="routine-header pm"><i className="fa-solid fa-moon"></i> Evening (PM) Routine</div>
                  <div className="step-list" id="pm-steps"></div>
                </div>

                <div className="routine-column" id="weekly-column" style={{ display: "none" }}>
                  <div className="routine-header weekly"><i className="fa-solid fa-calendar-week"></i> Weekly</div>
                  <div className="step-list" id="weekly-steps"></div>
                </div>
              </div>

              <div className="routine-notes" id="routine-notes" style={{ display: "none" }}></div>

              <div className="ingredients-summary">
                <h3><i className="fa-solid fa-vial"></i> Target Active Ingredients</h3>
                <div className="ingredient-tags" id="ingredient-tags"></div>
              </div>
            </div>
          </div>
        </section>

        <section id="doctor-tab" className="tab-pane" style={{ display: "none" }}>
          <div className="card doctor-card">
            <div className="card-header">
              <h2><i className="fa-solid fa-user-doctor"></i> Dermatologist &amp; Hospital Referral Portal</h2>
              <p>Connect directly with board-certified clinical dermatologists for Melasma &amp; High-Risk Skin Conditions</p>
            </div>

            <div className="doctors-grid" id="doctors-grid"></div>
          </div>
        </section>

        <section id="patients-tab" className="tab-pane" style={{ display: "none" }}>
          <div className="card">
            <div className="card-header">
              <h2><i className="fa-solid fa-hospital-user"></i> Patients &amp; Scan Records</h2>
              <p>Stored in PostgreSQL. Click a patient to see their history.</p>
            </div>
            <div className="metrics-grid" id="clinic-stats"></div>
            <div className="clinic-toolbar">
              <input className="clinic-input" id="patient-search" placeholder="Search by name, MRN, phone or email" />
              <button className="btn btn-primary btn-small" data-open-patient-modal=""><i className="fa-solid fa-user-plus"></i> New patient</button>
            </div>
            <div className="table-wrap">
              <table className="clinic-table">
                <thead><tr><th>Patient</th><th>Sex</th><th>Date of birth</th><th>Scans</th><th>Last score</th><th>Last scan</th><th>Research consent</th></tr></thead>
                <tbody id="patients-tbody"></tbody>
              </table>
            </div>
            <div id="patient-detail" className="card"></div>
          </div>
        </section>

        <section id="metrics-tab" className="tab-pane" style={{ display: "none" }}>
          <div className="card metrics-card">
            <div className="card-header">
              <h2><i className="fa-solid fa-hospital"></i> Hospital &amp; Clinical Partner Deliverables</h2>
              <p>Validation statistics, DINOv2+LoRA architectural specs, and safety metrics for hospital review</p>
            </div>

            <div className="metrics-grid">
              <div className="metric-card-stat">
                <div className="stat-icon"><i className="fa-solid fa-bullseye"></i></div>
                <div className="stat-info">
                  <span className="stat-val" id="stat-accuracy">—</span>
                  <span className="stat-lbl">Accuracy on never-seen faces</span>
                </div>
              </div>

              <div className="metric-card-stat">
                <div className="stat-icon"><i className="fa-solid fa-scale-balanced"></i></div>
                <div className="stat-info">
                  <span className="stat-val" id="stat-f1">—</span>
                  <span className="stat-lbl">Macro F1 Score</span>
                </div>
              </div>

              <div className="metric-card-stat">
                <div className="stat-icon"><i className="fa-solid fa-shield-heart"></i></div>
                <div className="stat-info">
                  <span className="stat-val" id="stat-sensitivity">—</span>
                  <span className="stat-lbl">Balanced accuracy</span>
                </div>
              </div>

              <div className="metric-card-stat">
                <div className="stat-icon"><i className="fa-solid fa-microchip"></i></div>
                <div className="stat-info">
                  <span className="stat-val" id="stat-params">—</span>
                  <span className="stat-lbl">Severity MAE (0–5 scale)</span>
                </div>
              </div>
            </div>

            <div className="confusion-matrix-section">
              <h3><i className="fa-solid fa-table-cells"></i> Confusion Matrix on never-seen faces (rows = true, columns = predicted)</h3>
              <div className="cm-table-container" id="cm-table-container"></div>
            </div>

            <div className="honest-box">
              <div className="compliance-header"><i className="fa-solid fa-circle-info"></i> Evaluation protocol</div>
              <ul id="eval-notes">
                <li>Every number above is measured on held-out faces that never appeared in training (images grouped by source face).</li>
                <li>Severity error is measured against dermatologist 0–5 grades from the labelled subset.</li>
                <li>Research prototype — not a cleared medical device. Final decisions rest with the clinician.</li>
              </ul>
            </div>

            <div className="confusion-matrix-section">
              <h3><i className="fa-solid fa-graduation-cap"></i> Continuous learning</h3>
              <div id="learning-panel"></div>
            </div>
          </div>
        </section>
      </main>

      <div className="modal" id="booking-modal" style={{ display: "none" }}>
        <div className="modal-content">
          <div className="modal-header">
            <h3><i className="fa-solid fa-video"></i> Schedule Tele-Dermatology Consultation</h3>
            <button className="close-modal" id="close-modal-btn">&times;</button>
          </div>
          <div className="modal-body">
            <div className="doc-summary-box" id="doc-summary-box"></div>

            <form id="booking-form">
              <div className="form-group">
                <label>Patient Full Name</label>
                <input type="text" id="patient-name" required placeholder="Enter full name" />
              </div>
              <div className="form-group">
                <label>Contact Phone / Email</label>
                <input type="text" id="patient-contact" required placeholder="Enter phone or email" />
              </div>
              <div className="form-group">
                <label>DINOv2 Melasma &amp; Biometrics Summary Attached</label>
                <input type="text" defaultValue="Auto-Attached: AI Melasma &amp; Clinical Triage Report" readOnly className="readonly-input" />
              </div>
              <button type="submit" className="btn btn-primary btn-block"><i className="fa-solid fa-calendar-check"></i> Confirm Instant Referral Appointment</button>
            </form>
            <div id="booking-confirmation" style={{ display: "none" }} className="booking-confirm-box"></div>
          </div>
        </div>
      </div>

      <div className="modal" id="patient-modal" style={{ display: "none" }}>
        <div className="modal-content">
          <div className="modal-header">
            <h3><i className="fa-solid fa-user-plus"></i> New patient</h3>
            <button className="close-modal" id="close-patient-modal">&times;</button>
          </div>
          <div className="modal-body">
            <form id="patient-form">
              <label className="full">Full name *<input name="full_name" required /></label>
              <label>MRN / hospital ID <em className="muted">(auto if blank)</em><input name="mrn" /></label>
              <label>Date of birth<input name="date_of_birth" type="date" /></label>
              <label>Sex<select name="sex"><option value="">—</option><option>Female</option><option>Male</option><option>Other</option></select></label>
              <label>Phone<input name="phone" /></label>
              <label className="full">Email<input name="email" type="email" /></label>
              <label className="full">Notes<textarea name="notes" rows={2}></textarea></label>
              <div id="patient-form-error"></div>
              <button type="submit" className="btn btn-primary full"><i className="fa-solid fa-floppy-disk"></i> Save patient</button>
            </form>
          </div>
        </div>
      </div>

      <Script src="/js/dashboard-guard.js?v=20261016" strategy="afterInteractive" />
      <Script src="/js/app.js" strategy="afterInteractive" />
      <Script src="/js/clinic.js" strategy="afterInteractive" />
    </>
  );
}
