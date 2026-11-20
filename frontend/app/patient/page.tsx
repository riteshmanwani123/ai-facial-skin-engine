"use client";

import Script from "next/script";

export default function PatientPortal() {
  return (
    <>
      <link rel="stylesheet" href="/css/style.css" />
      <link rel="stylesheet" href="/css/portal.css?v=20261120" />
      <link rel="stylesheet" href="/css/theme.css?v=20261016" />

      <header className="app-header">
        <div className="header-container">
          <div className="brand">
            <img src="/images/clarity_logo.jpg" alt="CLARITY" className="brand-logo-img" />
            <div className="brand-text">
              <span className="brand-name">Clarity <span className="highlight">by Dr Tanya</span></span>
              <span className="brand-tag">Skin insight. Doctor-led care.</span>
            </div>
          </div>
          <div className="header-actions">
            <a className="header-link" href="https://drtanya.in/collections/clarifying-range" target="_blank" rel="noopener">Shop ↗</a>
            <span className="session-bar" id="session-bar"></span>
          </div>
        </div>
      </header>

      <main className="portal-main">
        <div className="wizard-progress" id="wizard-progress">
          <span className="wp-step active" data-step="scan">01 Live scan</span>
          <span className="wp-step" data-step="report">02 AI report</span>
          <span className="wp-step" data-step="payment">03 Consultation</span>
          <span className="wp-step" data-step="pending">04 Doctor review</span>
          <span className="wp-step" data-step="completed">05 Your plan</span>
        </div>

        {/* STEP 1: guided live scan */}
        <section className="card wiz-step active" id="step-scan">
          <div className="card-header">
            <div className="eyebrow" id="scan-eyebrow">Clarity journey / Step 1 of 5</div>
            <h2 id="scan-title">Your facial scan, guided.</h2>
            <p>Five quick photos — front, left cheek, right cheek, chin &amp; neck, and forehead — so your doctor can compare the skin across your whole face. We take each photo for you; just follow the prompts.</p>
          </div>
          <div className="privacy-banner">
            <i className="fa-solid fa-eye-slash"></i>
            <div><strong>Your eyes are covered automatically.</strong> They are blacked out on this device before any photo is saved or sent, so we never receive an image of your eyes.</div>
          </div>
          <div className="view-chips" id="view-chips"></div>
          <div className="capture-prompt" id="capture-prompt"></div>
          <div className="camera-wrap" id="camera-wrap">
            <video id="cam-video" autoPlay playsInline muted></video>
            <canvas id="cam-live"></canvas>
            <div className="face-guide-oval"></div>
            <div className="cam-status" id="cam-status">Camera is off</div>
          </div>
          <div id="cam-error" className="form-error"></div>
          <div className="action-bar cam-actions">
            <button className="btn btn-secondary" id="cam-start"><i className="fa-solid fa-video"></i> Turn on camera</button>
            <button className="btn btn-primary" id="cam-capture" style={{ display: "none" }} disabled><i className="fa-solid fa-circle-dot"></i> Capture</button>
            <button className="btn btn-secondary" id="cam-skip" style={{ display: "none" }}><i className="fa-solid fa-forward"></i> Skip this angle</button>
            <button className="btn btn-primary" id="cam-analyze" style={{ display: "none" }}><i className="fa-solid fa-wand-magic-sparkles"></i> Analyze my skin</button>
          </div>
          <div className="upload-fallback">
            <span>No camera access? </span>
            <label className="link-btn">Upload a photo for this angle<input type="file" id="file-fallback" accept="image/*" style={{ display: "none" }} /></label>
          </div>
          <div className="loading-state" id="scan-loading" style={{ display: "none" }}>
            <div className="spinner"></div>
            <h3>Analyzing your skin…</h3>
          </div>
        </section>

        {/* STEP 2: AI report */}
        <section className="card wiz-step" id="step-report">
          <div className="card-header">
            <div className="eyebrow">Clarity journey / Step 2 of 5</div>
            <h2>Your scan summary, ready for review.</h2>
            <p id="report-subtitle">Screening result — not a medical diagnosis.</p>
          </div>
          <div className="note-box"><strong>AI summary — not a diagnosis.</strong> Visible features can have several explanations. A doctor reviews every result before any product or treatment is advised.</div>
          <div className="report-grid">
            <div id="report-views"></div>
            <div id="report-content"></div>
          </div>
          <div id="recommended-products"></div>
          <div className="consult-cta card">
            <h3>Get a doctor&apos;s diagnosis</h3>
            <p>Every AI scan is followed by a real board-certified dermatologist review: symptoms, diagnosis,
              treatment plan, prescription if needed, and a follow-up date to track your progress.</p>
            <div className="price-row"><span className="price">$99</span><span className="muted">one consultation, incl. a 30-day follow-up check</span></div>
            <button className="btn btn-primary full" id="start-consult-btn">Proceed to doctor consultation →</button>
          </div>
        </section>

        {/* STEP 3: payment (demo) */}
        <section className="card wiz-step" id="step-payment">
          <div className="card-header">
            <div className="eyebrow">Clarity journey / Step 3 of 5</div>
            <h2>Review your skin with a doctor.</h2>
            <p>Pick a time that suits you, Monday to Friday. This is a local research prototype — no real payment processor is connected.</p>
          </div>
          <div className="two-col">
            <div>
              <div className="panel" id="scheduler"></div>
              <div className="demo-payment-box" style={{ marginTop: 16 }}>
                <div className="dp-row"><span>Dermatologist consultation</span><strong>$99.00</strong></div>
                <div className="dp-note"><i className="fa-solid fa-circle-info"></i> DEMO ONLY — no card details are collected, no real charge occurs. This simply reserves your time and marks the case as paid so a doctor can review it.</div>
                <div id="pay-error" className="form-error"></div>
                <button className="btn btn-primary full" id="confirm-pay-btn" disabled><i className="fa-solid fa-lock"></i> Confirm time &amp; pay $99 (demo)</button>
              </div>
            </div>
            <div className="panel tinted" id="appt-summary"></div>
          </div>
        </section>

        {/* STEP 4: pending doctor review */}
        <section className="card wiz-step" id="step-pending">
          <div className="card-header">
            <div className="eyebrow">Clarity journey / Step 4 of 5</div>
            <h2>Your doctor is reviewing your case.</h2>
            <p>Your scan, AI report and intake details have been sent to the doctor queue.</p>
          </div>
          <div id="appt-card" style={{ marginTop: 16 }}></div>
          <div className="pending-box">
            <div className="spinner"></div>
            <p>This page updates automatically once your doctor completes the review.</p>
            <p className="muted">Demo tip: To review and prescribe as the doctor, open an Incognito tab or click here to sign in as the doctor: <a href="/login?as=doctor" target="_blank">Doctor Portal (1-Click Login)</a>.</p>
          </div>
        </section>

        {/* STEP 5: completed plan / follow-up */}
        <section className="card wiz-step" id="step-completed">
          <div className="card-header">
            <div className="eyebrow">Clarity journey / Step 5 of 5</div>
            <h2>Your plan, prescribed by your doctor.</h2>
          </div>
          <div className="plan-layout">
            <div id="plan-content"></div>
            <div>
              <div className="panel tinted" id="plan-side"></div>
              <div className="panel" id="followup-cta" style={{ marginTop: 16 }}>
                <h3>Check-in scans · Day 15, 30 &amp; 45</h3>
                <div id="followup-note" style={{ marginBottom: 12 }}></div>
                <button className="btn btn-primary full" id="followup-scan-btn"><i className="fa-solid fa-camera"></i> Do my follow-up scan now</button>
              </div>
            </div>
          </div>
          <div id="comparison-content"></div>
        </section>

        <p className="fine-print">AI supports review. Only a doctor approves prescriptions and changes.</p>
      </main>

      <Script src="/js/auth-guard.js?v=20261016" strategy="afterInteractive" />
      <Script src="/js/eye-safe-capture.js?v=20261016" strategy="afterInteractive" />
      <Script src="/js/scheduler.js?v=20261016" strategy="afterInteractive" />
      <Script src="/js/patient.js?v=20261120b" strategy="afterInteractive" />
    </>
  );
}
