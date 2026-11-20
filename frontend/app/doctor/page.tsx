"use client";

import Script from "next/script";

export default function DoctorPortal() {
  return (
    <>
      <link rel="stylesheet" href="/css/style.css" />
      <link rel="stylesheet" href="/css/clinic.css" />
      <link rel="stylesheet" href="/css/portal.css" />
      <link rel="stylesheet" href="/css/theme.css?v=20261016" />

      <header className="app-header">
        <div className="header-container">
          <div className="brand">
            <img src="/images/clarity_logo.jpg" alt="CLARITY" className="brand-logo-img" />
            <div className="brand-text">
              <span className="brand-name">CLARITY <span className="highlight">Doctor Portal</span></span>
              <span className="brand-tag">CASE QUEUE · DIAGNOSIS · CONTINUAL LEARNING</span>
            </div>
          </div>
          <nav className="nav-tabs">
            <button className="nav-btn active" data-tab="queue-tab"><i className="fa-solid fa-list-check"></i> Queue <span className="badge" id="queue-count"></span></button>
            <button className="nav-btn" data-tab="appts-tab"><i className="fa-solid fa-calendar-days"></i> Appointments <span className="badge" id="appts-count"></span></button>
            <button className="nav-btn" data-tab="followups-tab"><i className="fa-solid fa-calendar-day"></i> Follow-ups due <span className="badge" id="followups-count"></span></button>
            <button className="nav-btn" data-tab="history-tab"><i className="fa-solid fa-clock-rotate-left"></i> History</button>
          </nav>
          <div className="header-actions">
            <span className="session-bar" id="session-bar"></span>
          </div>
        </div>
      </header>

      <main className="portal-main wide">
        <section id="queue-tab" className="tab-pane active">
          <div className="card">
            <div className="card-header"><h2><i className="fa-solid fa-list-check"></i> Cases awaiting review</h2>
              <p>Paid consultations, ready for diagnosis. AI-drafted notes are provided as a starting point only.</p></div>
            <div className="queue-grid" id="queue-grid"></div>
          </div>
        </section>

        <section id="appts-tab" className="tab-pane" style={{ display: "none" }}>
          <div className="card">
            <div className="card-header"><h2>Appointments</h2>
              <p>Booked consultation times, Monday to Friday 10:00–19:00 IST in 20-minute slots (breaks excluded). Reviewing a case is never blocked by its appointment time.</p></div>
            <label className="check-inline"><input type="checkbox" id="appts-all" /> Show the whole clinic, not just my calendar</label>
            <div id="appts-list"></div>
          </div>
        </section>

        <section id="followups-tab" className="tab-pane" style={{ display: "none" }}>
          <div className="card">
            <div className="card-header"><h2><i className="fa-solid fa-calendar-day"></i> Follow-ups due</h2>
              <p>Completed cases where a Day 15 / 30 / 45 check-in scan is open and the patient has not done it yet.</p></div>
            <div className="queue-grid" id="followups-grid"></div>
          </div>
        </section>

        <section id="history-tab" className="tab-pane" style={{ display: "none" }}>
          <div className="card">
            <div className="card-header"><h2><i className="fa-solid fa-clock-rotate-left"></i> Completed consultations</h2></div>
            <div className="history-table-wrap">
              <table className="clinic-table">
                <thead><tr><th>Patient</th><th>Completed</th><th>Diagnosis</th><th>Follow-up</th><th>Result</th></tr></thead>
                <tbody id="history-tbody"></tbody>
              </table>
            </div>
          </div>
        </section>

        <section id="report-tab" className="tab-pane" style={{ display: "none" }}>
          <div className="card">
            <div className="card-header"><h2><i className="fa-solid fa-stethoscope"></i> Case report</h2>
              <button className="btn btn-secondary btn-small" id="back-to-queue" style={{ marginTop: 8 }}><i className="fa-solid fa-arrow-left"></i> Back to queue</button>
            </div>
            <div id="case-summary"></div>
            <form id="report-form" className="report-form-grid">
              <div className="ai-draft-note"><i className="fa-solid fa-wand-magic-sparkles"></i> Fields below are pre-filled from the AI scan. Review and edit everything before submitting — you are the diagnosis of record.</div>
              <label className="full">Symptoms<textarea name="symptoms" rows={2} required></textarea></label>
              <label className="full">Diagnosis<textarea name="diagnosis" rows={2} required></textarea></label>
              <label className="full">Cure / treatment plan<textarea name="cure" rows={2} required></textarea></label>
              <label className="full">Prescription<textarea name="prescription" rows={2} required></textarea></label>
              <label className="full">Previous condition / records<textarea name="previous_condition" rows={2}></textarea></label>
              <div className="note-box" style={{ marginTop: 0 }}><strong>Check-in scans: Day 15, 30 and 45.</strong> Counted in calendar days from the day you submit this report. The patient can scan only on those days, so the comparison shows the effect of your prescription, products and medicines.</div>
              <label>Confirm skin type<select name="verified_skin_type">
                <option value="dry">Dry</option><option value="oily">Oily</option>
                <option value="normal">Normal</option><option value="combination">Combination</option>
              </select></label>
              <div className="full">
                <div className="pf-label" style={{ marginBottom: 6 }}>Confirm/correct severity grades (0–5) — saved in the patient's record</div>
                <div className="sev-mini-grid" id="sev-mini-grid"></div>
              </div>
              <div id="report-error" className="form-error full"></div>
              <button type="submit" className="btn btn-primary full"><i className="fa-solid fa-check"></i> Submit report</button>
            </form>
          </div>
        </section>
      </main>

      <Script src="/js/auth-guard.js?v=20261016" strategy="afterInteractive" />
      <Script src="/js/doctor.js?v=20261016" strategy="afterInteractive" />
    </>
  );
}
