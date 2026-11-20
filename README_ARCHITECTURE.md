# Clarity AI Face Engine — architecture (v3)

```
Next.js  →  FastAPI  →  PostgreSQL  →  Image Storage  →  ML Service
frontend/   backend/     (Homebrew)      storage.py       ml_service.py
```

This replaced the previous Flask + SQLite + vanilla-JS app. Every route,
page and behavior is unchanged — same 3 pages (clinic dashboard, patient
portal, doctor portal), same API, same continual-learning loop — only the
framework and database engine changed. The old stack is kept working, side
by side, at [legacy_flask_sqlite_backup/](legacy_flask_sqlite_backup/) as a
reference/fallback; it is no longer the one to run day-to-day.

## Layers

| Layer | Where | Notes |
|---|---|---|
| **Next.js** | `frontend/` | 3 pages (`/`, `/patient`, `/doctor`) — the exact original HTML/CSS/JS, ported to React page shells. A `next.config.ts` rewrite proxies `/api/*` to FastAPI, so the original JS files' `fetch('/api/...')` calls work unchanged. |
| **FastAPI** | `backend/main.py` | Line-for-line port of the old `server.py` (kept at `legacy_flask_sqlite_backup/server_flask.py`). Every `/api/*` route is identical. |
| **PostgreSQL** | local Homebrew service, database `clarity` | `database.py` (project root) — same function signatures as the old SQLite version, so `train_v2.py`, `data_prep.py` and `continual_learning.py` needed no changes. |
| **Image Storage** | `storage.py` (project root) | Owns every scan image on disk (`data/scans/...`). Postgres only stores the path + sha256. Swapping to an object store later means changing only this file. |
| **ML Service** | `ml_service.py` (project root) | The DINOv2+LoRA model, CV metrics, triage rules and the instant-learning memory blend — an isolated module the FastAPI process calls in-process (not a separate network service, by design: a scan should never fail because "the ML service is down" on a single-user local prototype). |

## Running it

**One-time setup** (already done on this machine):
```bash
brew install postgresql@16 && brew services start postgresql@16
createdb clarity
cd frontend && npm install
```

**Every time** — three processes, in any order:
```bash
# 1. Postgres (usually already running as a brew service — check with:)
brew services list | grep postgresql

# 2. FastAPI backend (from the project root)
.venv/bin/python -m uvicorn backend.main:app --reload --port 8010

# 3. Next.js frontend (from frontend/)
cd frontend && npm run dev
```
Or just run `./dev.sh` from the project root, which starts FastAPI and Next.js together (Postgres is a persistent background service and does not need starting each time).

Then open:
- Clinic dashboard: http://localhost:3001
- Patient portal: http://localhost:3001/patient
- Doctor portal: http://localhost:3001/doctor

**Why port 3001, not 3000:** this machine already runs an unrelated app
(`frontend-uat`, a create-react-app project) bound to `127.0.0.1:3000`, while
Next.js's default dev server binds only the IPv6 wildcard. Both can be
"listening on port 3000" at once without erroring, and which one answers
`http://localhost:3000` depends on whether your browser/tool resolves
`localhost` to `127.0.0.1` or `::1` — a silent, easy-to-miss mix-up.
`frontend/package.json`'s `dev`/`start` scripts are pinned to `-p 3001` to
avoid this entirely.

`train_v2.py`, `data_prep.py` and `continual_learning.py` are run exactly as
before (`python train_v2.py`, etc.) — they now register models and read
data through the same Postgres database the app uses, so a fresh champion
model shows up in the running app automatically (the 30-second champion
watcher in `backend/main.py`), same as before.

## What changed vs. what didn't

- **Changed:** web framework (Flask → FastAPI), database engine (SQLite →
  PostgreSQL), frontend delivery (Jinja2 templates → Next.js pages), image
  storage is now its own module instead of being inlined in `database.py`.
- **Unchanged:** every API route and its behavior, the DINOv2+LoRA model
  and its training pipeline, the CSS/JS/UI (byte-for-byte the same files,
  now served from `frontend/public/`), the consultation/follow-up/
  continual-learning workflow, the demo-only $99 payment step.
- **New operational requirement:** Postgres must be running for anything
  that imports `database.py` — including `train_v2.py` and
  `continual_learning.py`, which previously needed nothing. `brew services
  start postgresql@16` starts it persistently; it survives reboots.

All existing data (2 patients, 4 scans, 2 consultations, both trained model
versions, the champion flag on model `2.1`) was migrated from the old
SQLite database via `migrate_sqlite_to_postgres.py` — nothing was lost.

## Accounts & login

Real sign-up/sign-in (`users` table, bcrypt-hashed passwords, JWT session in
an httpOnly cookie — `auth.py`) so multiple patients and clinicians can use
the app concurrently, each seeing only their own data, instead of the
anonymous intake-form-per-visit flow this app started with (which silently
created a new duplicate patient record on every visit — visible in the data
today as two separate "Aman Tadi" records from before this existed).

- **`/signup`** — choose patient or clinician. A patient signup creates the
  linked `patients` row in the same step (the account *is* the intake form
  now); a clinician signup is just an account.
- **`/login`** — redirects to `/patient` or `/doctor` based on the account's role.
- **`/patient`** and **`/doctor`** now require login (`requireAuth()` in
  `auth-guard.js`, redirects to `/login` if not signed in or the wrong
  role) — the patient portal's old manual "Your details" step is gone, it
  skips straight to the live scan using the account's own identity.
- `/api/analyze`, `/api/consultations`, and `/api/consultations/{id}/pay`
  auto-use the logged-in patient's own `patient_id` and reject attempts to
  act on another patient's consultation. `PATCH /api/consultations/{id}`
  (completing a diagnosis) now **requires** a clinician account and takes
  `doctor_name` from the authenticated session, not the request body — it
  can no longer be spoofed by whatever a client sends.
- The clinic dashboard (`/`) intentionally stays open (unauthenticated,
  using the existing free-text clinician-name header) — scoped out of this
  pass since it's the staff tool, not patient/doctor self-service.
- Implemented identically in both `backend/main.py` (FastAPI) and
  `server.py` (legacy Flask) — verified working in both.

**Doctor/clinician accounts are admin-provisioned, not self-service.**
`/signup` only creates patient accounts now — the `role` field is ignored
server-side even if a client sends `role: "clinician"` directly to the API
(verified: it silently becomes a patient account). An **`/admin`** page
(role=`admin`, same `requireAuth` gate) lets an admin create and remove
clinician accounts (`POST`/`GET`/`DELETE /api/admin/clinicians`); doctors
sign in at `/login` with the credentials an admin gave them.

Two demo accounts are seeded in the database for immediate testing:

| Role | Email | Password |
|---|---|---|
| Admin | `admin@clarity.local` | `Admin12345` |
| Clinician (demo doctor) | `doctor@clarity.local` | `Doctor12345` |

These are local demo credentials for this prototype only — change them (or
create real ones and delete these) before this ever leaves your machine.

## Product recommendation engine

`dr_tanya_products.py` holds Dr Tanya's real product catalog (scraped from
https://drtanya.in/collections/all on 2026-10-01 — 6 facial skincare items;
3 haircare products and a scalp-massager device were excluded as out of
scope for a facial-skin engine) and the matching logic: each product
declares which of the model's 18 severity attributes it targets and at what
weight, `recommend()` ranks the catalog against a scan's severity scores,
and `build_routine()` turns that into the AM/PM/weekly routine the UI
renders — real names, real prices, real "Shop now" links to drtanya.in, and
an explanation naming the exact AI-detected score that triggered each pick.
Retinol and the exfoliator device are automatically de-prioritized when
irritation/sensitivity is flagged. This replaced `dermatology_rules.py`'s
previous placeholder catalog of generic third-party brands (CeraVe, The
Ordinary, etc.), which was never real. Known catalog gaps (no cleanser, no
sunscreen, no dedicated anti-acne treatment) are surfaced as plain notes
rather than papered over with an invented product.

New endpoints: `GET /api/products` (full catalog) and
`GET /api/products/recommendations?scan_id=...` (ranked list with matched
concerns, independent of the AM/PM routine shape).

## Guided multi-angle scan & eye privacy

The patient scan is five live photos — **front, left cheek, right cheek, chin & neck (head back), forehead (head down)** —
and the patient's eyes are covered before any photo is stored. Two independent layers:

1. **Browser (primary, `static/js/eye-safe-capture.js`)** — MediaPipe Face Landmarker (478-point mesh, vendored in
   `frontend/public/vendor/mediapipe/`, runs on-device). Each frame is painted with solid black eye shapes *before* it is
   exported, so the live preview is already covered and an uncovered frame never exists in the saved file. The camera is
   **fail-closed**: no face / more than one face / eyes out of frame → capture is disabled. Head angle (from the mesh's pose
   matrix) must match the requested view; "Capture anyway" appears after 8 s. Uploads go through the same redaction.
2. **Server (safety net, `privacy.py`)** — OpenCV YuNet detector paints black ellipses again on every image reaching
   `/api/analyze`, adds the browser's own polygons, and **rejects (422) a patient's front photo it cannot confirm as covered**.
   (Python MediaPipe was rejected: it hard-aborts the whole process through a Metal helper.) On turned heads the server
   blacks out a wider "eye band" because YuNet's eye points drift there.

**Analysis copy.** Black patches change the AI's outputs, so the models read a skin-filled copy (`privacy.neutralize`, OpenCV
inpainting at ≤640 px) that is never stored or shown; the covered polygons are kept in `scan_views.redaction_json` so
retraining/re-embedding rebuilds the same copy. AI headline scoring uses the **front view only**; the other four angles are
stored (`scan_views`) and shown to the patient and doctor.

**Photo access.** `/api/scans/{id}/image` and `/views…` are served only to the owning patient and clinic staff.

**Hands-free capture.** The person only follows the on-screen direction ("turn a bit more to your right"…). Once the
angle is reached and held ~0.7 s, the session picks the sharpest, best-lit, best-centred frame of that hold and takes the
photo itself, then moves to the next angle; after 15 s on one angle it takes the closest frame so nobody gets stuck.
Manual "Capture now" and "Skip this angle" remain.

**Eyes at any head speed.** Detection and painting use the same canvas pixels; patches get a safety rim, are enlarged while the
head moves, and are unioned with the previous frame's patches. If no single face is tracked, the first frames after a
face is found, or the head moves too fast to trust the mesh, the whole frame is replaced by a coarse mosaic — never the
raw camera image. Measured with synthetic head motion: 0 visible eye pixels in 980 shown frames (770k eye-opening pixels).

### Measured limits (be honest with the client)
| Check | Result |
|---|---|
| Browser mesh covers the eye opening | 100% of 92 eyes on 46 real faces, incl. turned heads |
| Server fallback, frontal faces | iris centre covered ~100%, eye area ~94% |
| Server fallback, turned (>15°) faces | iris centre ~88%, eye area ~88% — **weaker, which is why the browser layer is the real guarantee** |
| Effect of covering on AI outputs (68 faces, skin-filled copy) | skin type changes in ~7%, mean severity shift ~0.23/5, acne grade changes in ~19% |

Covering the eyes is inherently lossy for eye-region attributes (dark circles, puffiness, crow's feet). Retraining on
covered images via the doctor-verdict loop (`continual_learning.py`) is the way to close that gap.

## Design system & sign-up questionnaire

**Look.** `static/css/theme.css` (copied to `frontend/public/css/`) is loaded last on every Next.js page and carries the
Figma journey look: paper background, thin-bordered white cards, Lora serif headings, ink-black buttons, grey note boxes
and a numbered step bar. It only restyles — IDs, classes and scripts are unchanged. Tokens are at the top of the file.
The patient page follows the journey layout (scan → AI summary → consultation → doctor review → plan & care timeline).
The legacy Flask templates (`templates/*.html`) do not load `theme.css` and keep their old look.

**Sign-up questionnaire.** `/signup` is a one-question-per-screen quiz (`static/js/signup-quiz.js`, `static/css/quiz.css`),
styled after the MDacne flow: 3-part progress bar, tap-to-answer pills (auto-advance), "Next" only on multi-select,
account step last, personalised summary at the end. Only questions that change what a dermatologist does are asked
(8–9 screens; breakout style and pregnancy are conditional) — see `intake.py` for why each is asked. Answers go with the
sign-up request as `intake`, are re-validated server-side (`intake.clean_intake`: unknown keys/values dropped), stored in
`patients.intake_json`, summarised into `patients.previous_skin_condition`, and shown to the doctor as "Patient questionnaire".

## Appointment scheduling (`scheduling.py`)

Booking a consultation now includes choosing an appointment. Rules live in the constants at the top of `scheduling.py`:
Monday–Friday, 10:00–19:00 IST, 3 slots an hour (20 min) = 27 slots a day, minus a lunch break (13:00–14:00) and a tea break
(16:20–16:40) = **23 bookable slots per doctor per day**. Breaks are returned as their own rows and shown on the picker.

- **Doctors.** Every clinician account has its own calendar; a slot is open while any doctor is free, and booking assigns the
  doctor with the lightest day. `appointments(doctor_id, start_at) WHERE status='booked'` is a unique index, so a double booking
  is impossible even under simultaneous requests (tested: 6 patients racing for one slot → exactly one wins).
- **Patient flow.** Step 3 shows the week picker; "Confirm time & pay" sends `POST /api/consultations/{id}/pay {slot_start}`, which
  reserves the slot and marks the demo payment in one request (a lost slot = 409 and nothing is paid). Step 4 shows the
  appointment with Reschedule / Cancel (until 2 h before). Cases without an appointment (older ones) get a "Book a time" button.
- **Rules.** Book from 60 min ahead to 28 days out; change/cancel until 2 h before (staff any time).
- **Doctor portal.** "Appointments" tab (my calendar / whole clinic), appointment chip on queue cards and case view. Reviewing a
  case is never blocked by its appointment time; completing a case marks its appointment completed.
- **Admin.** Clinic hours summary, closures (whole clinic or one doctor; all day or part of a day) that remove those slots
  from booking, and all upcoming appointments with cancel. A doctor with upcoming appointments cannot be removed.
- **API.** `GET /api/schedule/config|week`, `POST /api/consultations/{id}/appointment`, `GET /api/appointments`,
  `POST /api/appointments/{id}/cancel`, `GET|POST|DELETE /api/admin/schedule-blocks`. FastAPI only (the legacy Flask server has no scheduling).

## Roles, one-scan journey, learning off, production

**Roles & IDs.** Three roles — `patient`, `clinician` (doctor), `admin` — enforced by a database CHECK and by the API
(`require_roles` in `backend/main.py`). Every account has a unique ID from a Postgres sequence (PT-00001 patient, DR-00001 doctor,
AD-00001 admin); every patient record has a unique MRN (CL-000001). Admin → "All accounts" lists them.

| | patient | doctor | admin |
|---|---|---|---|
| Scan (`/api/analyze`, upload / drag-and-drop dashboard) | own, by journey rules | no | yes |
| Clinic dashboard `/`, patient records, model reports, learning | no | no | yes |
| Cases, appointments, reports, scan photos | own only | all | all |
| Accounts, closures, appointment management | no | no | yes |

**One scan, then the consultation (`journey.py`).** A patient gets one scan + report; a second scan is refused until the
consultation is done. After the doctor completes the case, check-in scans open on **day 15, 30 and 45** — calendar days from the
completion date (clinic time, IST). Each has a window (15→30, 30→45, 45→60) and can be done once; an unused window is "missed".
After the last check-in a new cycle may start. The server (`GET /api/me/journey`) decides which screen the patient is on, so
a refresh or a new device always resumes in the right place. Check-in scans are compared with the first scan.

**Learning from user data is OFF.** `CLARITY_LEARNING` defaults to `0`: no instant-learning memory, no retraining, `/api/learning/retrain`
returns 403, doctor verdicts are stored in the patient's record only, and sign-up no longer asks for research consent.

**Production run.** `./prod.sh` (builds and serves; FastAPI without reload on :8010, Next on :3001). Regression tests in
`tests/` (run with `PYTHONPATH=. .venv/bin/python tests/<file>.py` against the running API).
The legacy Flask `server.py` is **not** hardened with these rules — do not run it alongside production.
