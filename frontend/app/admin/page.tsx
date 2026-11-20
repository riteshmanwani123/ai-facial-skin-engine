"use client";

import Script from "next/script";

export default function AdminPage() {
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
              <span className="brand-name">CLARITY <span className="highlight">Admin</span></span>
              <span className="brand-tag">MANAGE DOCTOR &amp; CLINIC STAFF ACCOUNTS</span>
            </div>
          </div>
          <div className="header-actions">
            <span className="session-bar" id="session-bar"></span>
            <a className="btn btn-secondary btn-small" href="/"><i className="fa-solid fa-hospital"></i> Clinic dashboard</a>
          </div>
        </div>
      </header>

      <main className="portal-main">
        <section className="card">
          <div className="card-header">
            <h2><i className="fa-solid fa-user-doctor"></i> Add a doctor / clinician account</h2>
            <p>Doctors and clinic staff can&apos;t self-register — create their login here and share the credentials with them directly. They can change their password later from their own account (not yet implemented in this prototype).</p>
          </div>
          <form id="add-clinician-form" className="auth-form">
            <label>Full name<input type="text" name="full_name" required placeholder="Dr. Jane Smith" /></label>
            <label>Email (their login username)<input type="email" name="email" required autoComplete="off" /></label>
            <label>Temporary password (min. 8 characters)<input type="text" name="password" required minLength={8} placeholder="Share this with them securely" /></label>
            <div id="add-clinician-error" className="form-error"></div>
            <button type="submit" className="btn btn-primary full"><i className="fa-solid fa-user-plus"></i> Create clinician account</button>
          </form>
        </section>

        <section className="card" style={{ marginTop: 20 }}>
          <div className="card-header">
            <h2><i className="fa-solid fa-list"></i> Existing doctor / clinician accounts</h2>
          </div>
          <div className="table-wrap">
            <table className="clinic-table">
              <thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Created</th><th>Last login</th><th></th></tr></thead>
              <tbody id="clinicians-tbody"></tbody>
            </table>
          </div>
        </section>

        <section className="card" style={{ marginTop: 20 }}>
          <div className="card-header">
            <div className="eyebrow">Reservations</div>
            <h2>Clinic hours &amp; closures</h2>
            <p id="sched-summary">Loading schedule…</p>
          </div>
          <form id="block-form" className="auth-form" style={{ marginTop: 14 }}>
            <div className="block-grid">
              <label>From date<input type="date" name="date_from" required /></label>
              <label>To date <em className="muted">(optional)</em><input type="date" name="date_to" /></label>
              <label>From time <em className="muted">(blank = all day)</em><input type="time" name="start_time" /></label>
              <label>To time<input type="time" name="end_time" /></label>
              <label>Applies to<select name="doctor_id" id="block-doctor"><option value="">Whole clinic</option></select></label>
              <label>Reason<input type="text" name="reason" maxLength={200} placeholder="Public holiday, leave, training…" /></label>
            </div>
            <div id="block-error" className="form-error"></div>
            <button type="submit" className="btn btn-primary">Block this time</button>
          </form>
          <div className="table-wrap" style={{ marginTop: 16 }}>
            <table className="clinic-table">
              <thead><tr><th>When</th><th>Applies to</th><th>Reason</th><th></th></tr></thead>
              <tbody id="blocks-tbody"></tbody>
            </table>
          </div>
        </section>

        <section className="card" style={{ marginTop: 20 }}>
          <div className="card-header">
            <div className="eyebrow">Reservations</div>
            <h2>Capacity this week</h2>
            <p>Booked vs available slots per doctor, Monday to Friday (breaks and closures excluded).</p>
          </div>
          <div className="week-nav"><button type="button" className="link-btn" id="cap-prev">‹ Previous week</button><span id="cap-week" className="muted"></span><button type="button" className="link-btn" id="cap-next">Next week ›</button></div>
          <div className="table-wrap"><table className="clinic-table"><thead id="cap-head"></thead><tbody id="cap-body"></tbody></table></div>
        </section>

        <section className="card" style={{ marginTop: 20 }}>
          <div className="card-header">
            <h2>Cases waiting for a time</h2>
            <p>Paid consultations with no booked appointment (older cases, or one that was cancelled). Assign a time on the patient&apos;s behalf.</p>
          </div>
          <div className="table-wrap"><table className="clinic-table"><thead><tr><th>Patient</th><th>Paid</th><th></th></tr></thead><tbody id="needs-tbody"></tbody></table></div>
        </section>

        <section className="card" style={{ marginTop: 20 }}>
          <div className="card-header">
            <h2>Appointments</h2>
            <p>Every booking, any status. Reschedule or cancel for a patient — staff are not bound by the 2-hour cutoff.</p>
          </div>
          <form id="appt-filter" className="auth-form" style={{ marginTop: 12 }}>
            <div className="block-grid">
              <label>From date<input type="date" name="date_from" /></label>
              <label>To date<input type="date" name="date_to" /></label>
              <label>Doctor<select name="doctor_id" id="appt-doctor"><option value="">All doctors</option></select></label>
              <label>Status<select name="status"><option value="">All</option><option value="booked">Booked</option><option value="completed">Completed</option><option value="cancelled">Cancelled</option></select></label>
              <label>Patient name<input type="text" name="q" placeholder="Search…" /></label>
              <label>&nbsp;<button type="submit" className="btn btn-secondary">Apply filters</button></label>
            </div>
          </form>
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table className="clinic-table">
              <thead><tr><th>When</th><th>Patient</th><th>Doctor</th><th>Status</th><th></th></tr></thead>
              <tbody id="appts-tbody"></tbody>
            </table>
          </div>
          <div id="admin-resched" className="panel" style={{ display: "none", marginTop: 16 }}>
            <div className="sched-modal-head"><h3 id="admin-resched-title">Reschedule</h3><button type="button" className="link-btn" id="admin-resched-close">Close</button></div>
            <div id="admin-resched-root"></div>
            <div id="admin-resched-err" className="form-error"></div>
            <button type="button" className="btn btn-primary" id="admin-resched-confirm" disabled style={{ marginTop: 12 }}>Confirm this time</button>
          </div>
        </section>

        <section className="card" style={{ marginTop: 20 }}>
          <div className="card-header">
            <div className="eyebrow">Roles &amp; IDs</div>
            <h2>All accounts</h2>
            <p>Every account has a unique ID and one role: patient (PT-), doctor (DR-) or admin (AD-). Patients also have a record number (CL-).</p>
          </div>
          <label className="check-inline" style={{ marginTop: 0 }}>Show <select id="users-role"><option value="">everyone</option><option value="patient">patients</option><option value="clinician">doctors</option><option value="admin">admins</option></select></label>
          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table className="clinic-table"><thead><tr><th>ID</th><th>Role</th><th>Name</th><th>Email</th><th>Record no.</th><th>Last login</th></tr></thead><tbody id="users-tbody"></tbody></table>
          </div>
        </section>
      </main>

      <Script src="/js/auth-guard.js?v=20261016" strategy="afterInteractive" />
      <Script src="/js/scheduler.js?v=20261016" strategy="afterInteractive" />
      <Script src="/js/admin.js?v=20261016" strategy="afterInteractive" />
    </>
  );
}
