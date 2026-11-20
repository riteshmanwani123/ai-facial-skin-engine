"""
journey.py — the patient's journey rules: who may scan, and when.

One scan per consultation cycle:

    first scan + report  ->  paid consultation (with an appointment)  ->  doctor completes the case
        ->  check-in scans on day 15, day 30 and day 45  ->  (cycle over) a fresh first scan may start the next one.

* A patient cannot scan again before the consultation: a first scan that has not been consulted on blocks another one,
  and so does a consultation that is still open.
* Check-ins are counted in CALENDAR days from the day the doctor completed the case (clinic time, IST): day 15 opens at
  00:00 IST on completion date + 15 days. Each check-in has a window — day 15 from +15 until +30, day 30 from +30 until
  +45, day 45 from +45 for 15 days — and can be done once. A window that passes unused is "missed".
* Staff (admin) are not bound by these rules; they scan from the clinic dashboard.

No machine learning is involved here; it is bookkeeping.
"""
from datetime import datetime, date, time, timedelta, timezone

from scheduling import TZ, parse, iso, day_label

FOLLOWUP_DAYS = (15, 30, 45)
LAST_WINDOW_DAYS = 15
UTC = timezone.utc


def _base_date(con):
    stamp = con.get("completed_at") or con.get("paid_at") or con.get("created_at")
    return parse(stamp).astimezone(TZ).date()


def opens_at(completed_dt, day):
    """UTC ISO time at which the day-N check-in opens: IST midnight, N calendar days after the completion date."""
    d = completed_dt.astimezone(TZ).date() + timedelta(days=day)
    return iso(datetime.combine(d, time(0, 0), tzinfo=TZ))


def today_ist(now=None):
    return (now or datetime.now(UTC)).astimezone(TZ).date()


def checkpoints(con, done=None, today=None):
    """The three check-ins of a completed consultation. `done` maps day -> scan_id. status is one of
    done | available | upcoming | missed."""
    done = done or {}
    today = today or today_ist()
    base = _base_date(con)
    out = []
    for i, day in enumerate(FOLLOWUP_DAYS):
        opens = base + timedelta(days=day)
        closes = base + timedelta(days=FOLLOWUP_DAYS[i + 1] if i + 1 < len(FOLLOWUP_DAYS) else day + LAST_WINDOW_DAYS)
        if day in done:
            status = "done"
        elif today < opens:
            status = "upcoming"
        elif today < closes:
            status = "available"
        else:
            status = "missed"
        opens_dt = datetime.combine(opens, time(0, 0), tzinfo=TZ)
        out.append({"day": day, "label": f"Day {day}", "status": status, "opens_on": opens.isoformat(),
                    "closes_on": closes.isoformat(), "opens_label": day_label(opens_dt),
                    "closes_label": day_label(datetime.combine(closes - timedelta(days=1), time(0, 0), tzinfo=TZ)),
                    "scan_id": done.get(day)})
    return out


def program_over(cps, today=None):
    """True when no check-in can happen any more: all done/missed (the last window has closed or the last one is done)."""
    return all(c["status"] in ("done", "missed") for c in cps)


def available_checkpoint(cps):
    return next((c for c in cps if c["status"] == "available"), None)


def next_checkpoint(cps):
    return next((c for c in cps if c["status"] in ("available", "upcoming")), None)


def state_for_patient(patient_id, db, today=None):
    """Where the patient is in the cycle and what they may do next. Pure data; the API turns it into screens.

    stage: new | report | payment | pending | completed
      new        no scan in the current cycle  -> may take the first scan
      report     scanned, not yet consulted    -> must continue to the consultation (no second scan)
      payment    consultation created, awaiting payment/appointment
      pending    paid; waiting for the doctor
      completed  doctor finished; check-ins on day 15/30/45
    """
    today = today or today_ist()
    cons = db.patient_consultations(patient_id)
    baselines = db.patient_baseline_scans(patient_id)
    consulted = {c["scan_id"] for c in cons}
    latest = cons[0] if cons else None
    newest_unconsulted = next((s for s in baselines if s["id"] not in consulted
                               and (latest is None or s["captured_at"] > latest["created_at"])), None)
    if newest_unconsulted:
        return {"stage": "report", "scan_id": newest_unconsulted["id"], "consultation": None, "checkpoints": [], "can_start_new": False}
    if latest is None:
        return {"stage": "new", "scan_id": None, "consultation": None, "checkpoints": [], "can_start_new": True}
    if latest["status"] == "awaiting_payment":
        return {"stage": "payment", "scan_id": latest["scan_id"], "consultation": latest, "checkpoints": [], "can_start_new": False}
    if latest["status"] == "awaiting_doctor":
        return {"stage": "pending", "scan_id": latest["scan_id"], "consultation": latest, "checkpoints": [], "can_start_new": False}
    done = {f["day"]: f["scan_id"] for f in db.followups_for([latest["id"]]).get(latest["id"], [])}
    cps = checkpoints(latest, done, today)
    return {"stage": "completed", "scan_id": latest["scan_id"], "consultation": latest, "checkpoints": cps,
            "can_start_new": program_over(cps, today)}


def enrich_completed(consultations, db, today=None):
    """Adds 'checkpoints' (and 'due_checkpoint') to completed consultations — for the doctor lists."""
    today = today or today_ist()
    comp = [c for c in consultations if c.get("status") == "completed"]
    done = db.followups_for([c["id"] for c in comp])
    for c in comp:
        d = {f["day"]: f["scan_id"] for f in done.get(c["id"], [])}
        c["checkpoints"] = checkpoints(c, d, today)
        c["due_checkpoint"] = available_checkpoint(c["checkpoints"])
    return consultations
