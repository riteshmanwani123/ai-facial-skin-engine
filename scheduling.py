"""
scheduling.py — consultation reservations for Clarity by Dr. Tanya.

The rules (all in the constants below, so the clinic can change them in one place):
  * Open Monday to Friday, 10:00 to 19:00, clinic time (Asia/Kolkata).
  * 3 appointments an hour = 20-minute slots, 27 slots a day, minus the breaks:
        13:00-14:00  lunch break   (3 slots)
        16:20-16:40  tea break     (1 slot)
    -> 23 bookable slots per doctor per day. Breaks are returned as their own rows so the screen can show them.
  * Every clinician account is a doctor with their own calendar. A slot is bookable while at least one doctor is free
    in it; booking assigns the doctor with the lightest day (ties broken by id). The unique index on
    appointments(doctor_id, start_at) WHERE status='booked' is what actually prevents a double booking — the checks
    in Python only make the errors friendly.
  * You can book from LEAD_MIN minutes from now up to HORIZON_DAYS ahead. A patient can reschedule or cancel until
    CHANGE_CUTOFF_H hours before the start; staff can always.
  * Closures (public holidays, a doctor's leave) are rows in schedule_blocks — clinic-wide when doctor_id is NULL.

Times are stored as UTC ISO strings; everything shown to people is formatted in clinic time with an explicit "IST".
"""
import uuid
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

import database as db

TZ = ZoneInfo("Asia/Kolkata")
TZ_LABEL = "IST"
OPEN, CLOSE = time(10, 0), time(19, 0)
SLOT_MIN = 20
WORKDAYS = (0, 1, 2, 3, 4)                      # Monday..Friday
BREAKS = [(time(13, 0), time(14, 0), "Lunch break"), (time(16, 20), time(16, 40), "Tea break")]
LEAD_MIN = 60
HORIZON_DAYS = 28
CHANGE_CUTOFF_H = 2
UTC = timezone.utc


def _mins(t):
    return t.hour * 60 + t.minute


# Breaks must sit on the slot grid, otherwise "3 slots an hour" would silently not hold.
for _s, _e, _n in BREAKS:
    assert (_mins(_s) - _mins(OPEN)) % SLOT_MIN == 0 and (_mins(_e) - _mins(OPEN)) % SLOT_MIN == 0, f"break {_n} is off the slot grid"
assert 60 % SLOT_MIN == 0


class ScheduleError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------------------------- time helpers
def now_utc():
    return datetime.now(UTC)


def iso(dt):
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def parse(s):
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ScheduleError("That time isn't valid")
    if dt.tzinfo is None:
        raise ScheduleError("Time must include a timezone")
    return dt.astimezone(UTC)


def local(dt):
    return dt.astimezone(TZ)


def t12(dt):
    return local(dt).strftime("%I:%M %p").lstrip("0")


def day_label(dt):
    d = local(dt)
    return f"{d:%a} {d.day} {d:%b}"


def _at(d, t):
    return datetime.combine(d, t, tzinfo=TZ)


def monday_of(d):
    return d - timedelta(days=d.weekday())


# ---------------------------------------------------------------------------------------------- the day template
def day_items(d):
    """The day's rows in order: {'kind':'slot','start','end'} and {'kind':'break','start','end','label'} (tz-aware, clinic time).
    Empty on weekends."""
    if d.weekday() not in WORKDAYS:
        return []
    items, cur, end = [], _at(d, OPEN), _at(d, CLOSE)
    step = timedelta(minutes=SLOT_MIN)
    while cur < end:
        brk = next((b for b in BREAKS if _at(d, b[0]) <= cur < _at(d, b[1])), None)
        if brk:
            items.append({"kind": "break", "start": _at(d, brk[0]), "end": _at(d, brk[1]), "label": brk[2]})
            cur = _at(d, brk[1])
            continue
        items.append({"kind": "slot", "start": cur, "end": cur + step})
        cur += step
    return items


def slots_per_day():
    return sum(1 for i in day_items(date(2026, 1, 5)) if i["kind"] == "slot")


def config():
    return {
        "timezone": "Asia/Kolkata", "tz_label": TZ_LABEL, "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
        "open": OPEN.strftime("%H:%M"), "close": CLOSE.strftime("%H:%M"), "slot_minutes": SLOT_MIN,
        "slots_per_hour": 60 // SLOT_MIN, "slots_per_day": slots_per_day(),
        "breaks": [{"from": b[0].strftime("%H:%M"), "to": b[1].strftime("%H:%M"), "label": b[2]} for b in BREAKS],
        "lead_minutes": LEAD_MIN, "horizon_days": HORIZON_DAYS, "change_cutoff_hours": CHANGE_CUTOFF_H,
    }


def _earliest(now=None):
    return (now or now_utc()) + timedelta(minutes=LEAD_MIN)


def _latest(now=None):
    return (now or now_utc()) + timedelta(days=HORIZON_DAYS)


def is_valid_slot(start_utc):
    """True when start is one of the template's bookable slots (right day, on the grid, not in a break)."""
    st = local(start_utc)
    return any(i["kind"] == "slot" and i["start"] == st for i in day_items(st.date()))


# ---------------------------------------------------------------------------------------------- data access
def _doctors(c):
    return [r for r in c.execute("SELECT id, full_name FROM users WHERE role = 'clinician' ORDER BY id").fetchall()]


def _range_rows(c, lo_utc, hi_utc):
    booked = c.execute("SELECT doctor_id, start_at FROM appointments WHERE status = 'booked' AND start_at >= %s AND start_at < %s",
                       (iso(lo_utc), iso(hi_utc))).fetchall()
    blocks = c.execute("SELECT doctor_id, start_at, end_at, reason FROM schedule_blocks WHERE end_at > %s AND start_at < %s",
                       (iso(lo_utc), iso(hi_utc))).fetchall()
    return booked, blocks


def _block_covering(blocks, start_utc, end_utc, doctor_id=None):
    """A block that overlaps the slot and applies to the clinic (doctor_id None) or to this doctor."""
    s, e = iso(start_utc), iso(end_utc)
    for b in blocks:
        if b["start_at"] < e and b["end_at"] > s and (b["doctor_id"] is None or b["doctor_id"] == doctor_id):
            return b
    return None


# ---------------------------------------------------------------------------------------------- availability
def week(date_str=None, now=None):
    """Mon-Fri of the week containing date_str with every slot's state — the data behind the picker."""
    now = now or now_utc()
    today = local(now).date()
    try:
        d0 = date.fromisoformat(date_str) if date_str else today
    except ValueError:
        raise ScheduleError("date must be YYYY-MM-DD")
    mon = monday_of(max(d0, today))
    last_ok = monday_of(local(_latest(now)).date())
    if mon > last_ok:
        mon = last_ok
    lo, hi = _at(mon, time(0, 0)).astimezone(UTC), _at(mon + timedelta(days=7), time(0, 0)).astimezone(UTC)
    earliest, latest = _earliest(now), _latest(now)
    with db.connect() as c:
        doctors = _doctors(c)
        booked, blocks = _range_rows(c, lo, hi)
    taken = {}
    for b in booked:
        taken.setdefault(b["start_at"], set()).add(b["doctor_id"])

    days = []
    for k in range(5):
        d = mon + timedelta(days=k)
        items, avail, closed_reason = [], 0, None
        for it in day_items(d):
            if it["kind"] == "break":
                items.append({"kind": "break", "label": it["label"], "time": it["start"].strftime("%H:%M"),
                              "range": f"{t12(it['start'])} – {t12(it['end'])}"})
                continue
            s_utc, e_utc = it["start"].astimezone(UTC), it["end"].astimezone(UTC)
            clinic_block = _block_covering(blocks, s_utc, e_utc)
            if s_utc < earliest or s_utc > latest:
                status = "past" if s_utc < earliest else "later"
            elif clinic_block:
                status, closed_reason = "closed", clinic_block["reason"] or "Clinic closed"
            else:
                free = [dr for dr in doctors if dr["id"] not in taken.get(iso(s_utc), set())
                        and not _block_covering(blocks, s_utc, e_utc, dr["id"])]
                status = "available" if free else "full"
            if status == "available":
                avail += 1
            items.append({"kind": "slot", "start": iso(s_utc), "end": iso(e_utc), "time": it["start"].strftime("%H:%M"),
                          "label": t12(it["start"]), "status": status})
        slot_states = [i["status"] for i in items if i["kind"] == "slot"]
        days.append({"date": d.isoformat(), "weekday": f"{d:%a}", "label": f"{d:%a} {d.day} {d:%b}", "day": d.day, "month": f"{d:%b}",
                     "available": avail, "closed_reason": closed_reason, "is_today": d == today, "items": items,
                     "past": bool(slot_states) and all(x in ("past", "later") for x in slot_states)})
    prev_mon = mon - timedelta(days=7)
    return {
        "week_start": mon.isoformat(), "days": days, "tz_label": TZ_LABEL, "slot_minutes": SLOT_MIN,
        "prev": prev_mon.isoformat() if prev_mon >= monday_of(today) else None,
        "next": (mon + timedelta(days=7)).isoformat() if mon + timedelta(days=7) <= last_ok else None,
        "doctors": len(doctors), "next_available": next_available(now),
    }


def next_available(now=None):
    """The soonest bookable slot, for the 'Next available' shortcut. None when nothing is open."""
    now = now or now_utc()
    earliest, latest = _earliest(now), _latest(now)
    with db.connect() as c:
        doctors = _doctors(c)
        booked, blocks = _range_rows(c, earliest, latest + timedelta(days=1))
    if not doctors:
        return None
    taken = {}
    for b in booked:
        taken.setdefault(b["start_at"], set()).add(b["doctor_id"])
    d = local(earliest).date()
    while d <= local(latest).date():
        for it in day_items(d):
            if it["kind"] != "slot":
                continue
            s_utc, e_utc = it["start"].astimezone(UTC), it["end"].astimezone(UTC)
            if s_utc < earliest or s_utc > latest or _block_covering(blocks, s_utc, e_utc):
                continue
            if any(dr["id"] not in taken.get(iso(s_utc), set()) and not _block_covering(blocks, s_utc, e_utc, dr["id"]) for dr in doctors):
                return {"start": iso(s_utc), "label": f"{day_label(s_utc)} · {t12(s_utc)} {TZ_LABEL}"}
        d += timedelta(days=1)
    return None


# ---------------------------------------------------------------------------------------------- appointments
def _present(r, now=None):
    """Appointment row -> what the UI needs."""
    if not r:
        return None
    now = now or now_utc()
    st, en = parse(r["start_at"]), parse(r["end_at"])
    return {
        "id": r["id"], "consultation_id": r["consultation_id"], "patient_id": r["patient_id"], "doctor_id": r["doctor_id"],
        "doctor_name": r.get("doctor_name"), "patient_name": r.get("patient_name"), "status": r["status"],
        "start": iso(st), "end": iso(en), "date": local(st).date().isoformat(), "day_label": day_label(st),
        "time_label": f"{t12(st)} – {t12(en)} {TZ_LABEL}", "start_label": t12(st), "tz_label": TZ_LABEL,
        "minutes": SLOT_MIN, "can_change": r["status"] == "booked" and st - now >= timedelta(hours=CHANGE_CUTOFF_H),
        "cancel_reason": r.get("cancel_reason"),
    }


_SELECT = ("SELECT a.*, u.full_name AS doctor_name, p.full_name AS patient_name FROM appointments a "
           "LEFT JOIN users u ON u.id = a.doctor_id LEFT JOIN patients p ON p.id = a.patient_id ")


def get(appointment_id):
    with db.connect() as c:
        return _present(c.execute(_SELECT + "WHERE a.id = %s", (appointment_id,)).fetchone())


def for_consultation(cid):
    """The consultation's live appointment, else its most recent one (so 'cancelled' can still be shown)."""
    with db.connect() as c:
        r = c.execute(_SELECT + "WHERE a.consultation_id = %s ORDER BY (a.status = 'booked') DESC, a.created_at DESC LIMIT 1", (cid,)).fetchone()
    return _present(r)


def attach(consultations):
    """Adds an 'appointment' key to each consultation dict (one query for the whole list)."""
    ids = [c["id"] for c in consultations]
    if not ids:
        return consultations
    with db.connect() as c:
        rows = c.execute(_SELECT + "WHERE a.consultation_id = ANY(%s) ORDER BY a.created_at", (ids,)).fetchall()
    best = {}
    for r in rows:   # a booked row beats a cancelled one; otherwise the newest wins
        cur = best.get(r["consultation_id"])
        if cur is None or (r["status"] == "booked") or cur["status"] != "booked":
            best[r["consultation_id"]] = r
    for con in consultations:
        con["appointment"] = _present(best.get(con["id"]))
    return consultations


def list_upcoming(doctor_id=None, patient_id=None, include_past_days=0, limit=300):
    since = now_utc() - timedelta(days=include_past_days) if include_past_days else now_utc() - timedelta(minutes=SLOT_MIN)
    q, args = _SELECT + "WHERE a.status = 'booked' AND a.start_at >= %s", [iso(since)]
    if doctor_id:
        q += " AND a.doctor_id = %s"
        args.append(doctor_id)
    if patient_id:
        q += " AND a.patient_id = %s"
        args.append(patient_id)
    q += " ORDER BY a.start_at LIMIT %s"
    args.append(limit)
    with db.connect() as c:
        return [_present(r) for r in c.execute(q, args).fetchall()]


def book(consultation_id, patient_id, start_iso, actor="patient", staff=False, now=None):
    """Reserve start_iso for the consultation (replacing its current booking if it has one — a reschedule).
    Raises ScheduleError (409 when the slot has just been taken). Returns the appointment."""
    now = now or now_utc()
    start = parse(start_iso)
    end = start + timedelta(minutes=SLOT_MIN)
    if not is_valid_slot(start):
        raise ScheduleError("That isn't a bookable time. Appointments run Monday to Friday, 10:00 to 19:00, in 20-minute slots.")
    if start < _earliest(now):
        raise ScheduleError(f"Please pick a time at least {LEAD_MIN} minutes from now.")
    if start > _latest(now):
        raise ScheduleError(f"Appointments can be booked up to {HORIZON_DAYS} days ahead.")

    with db.connect() as c:
        old = c.execute("SELECT * FROM appointments WHERE consultation_id = %s AND status = 'booked'", (consultation_id,)).fetchone()
        if old:
            if parse(old["start_at"]) == start:
                raise ScheduleError("That is already your appointment time.")
            if not staff and parse(old["start_at"]) - now < timedelta(hours=CHANGE_CUTOFF_H):
                raise ScheduleError(f"Appointments can only be changed up to {CHANGE_CUTOFF_H} hours before they start.", 409)
        doctors = _doctors(c)
        if not doctors:
            raise ScheduleError("No doctors are available to take bookings right now.", 409)
        _, blocks = _range_rows(c, start, end)
        if _block_covering(blocks, start, end):
            raise ScheduleError("The clinic is closed at that time. Please pick another.", 409)
        day_lo = _at(local(start).date(), time(0, 0)).astimezone(UTC)
        load = {r["doctor_id"]: r["n"] for r in c.execute(
            "SELECT doctor_id, COUNT(*) AS n FROM appointments WHERE status='booked' AND start_at >= %s AND start_at < %s GROUP BY doctor_id",
            (iso(day_lo), iso(day_lo + timedelta(days=1)))).fetchall()}
        ts, appt_id, chosen = db.now(), str(uuid.uuid4()), None
        for dr in sorted(doctors, key=lambda d: (load.get(d["id"], 0), d["id"])):
            if _block_covering(blocks, start, end, dr["id"]):
                continue
            try:
                with c.transaction():       # savepoint: a lost race on one doctor must not poison the rest
                    c.execute("INSERT INTO appointments(id, consultation_id, patient_id, doctor_id, start_at, end_at, status, created_at, updated_at)"
                              " VALUES (%s,%s,%s,%s,%s,%s,'booked',%s,%s)",
                              (appt_id, consultation_id, patient_id, dr["id"], iso(start), iso(end), ts, ts))
                chosen = dr
                break
            except psycopg.errors.UniqueViolation:
                continue
        if not chosen:
            raise ScheduleError("Sorry — that time was just taken. Please choose another.", 409)
        if old:
            c.execute("UPDATE appointments SET status='cancelled', cancel_reason='rescheduled', updated_at=%s WHERE id=%s", (ts, old["id"]))
        db.audit("reschedule" if old else "book", "appointment", appt_id, actor,
                 {"consultation": consultation_id, "start": iso(start), "doctor": chosen["id"], "from": old["start_at"] if old else None}, conn=c)
    return get(appt_id)


def cancel(appointment_id, actor="patient", staff=False, reason="cancelled by patient", now=None):
    now = now or now_utc()
    with db.connect() as c:
        r = c.execute("SELECT * FROM appointments WHERE id = %s", (appointment_id,)).fetchone()
        if not r:
            raise ScheduleError("Appointment not found", 404)
        if r["status"] != "booked":
            raise ScheduleError(f"This appointment is already {r['status']}.", 409)
        if not staff and parse(r["start_at"]) - now < timedelta(hours=CHANGE_CUTOFF_H):
            raise ScheduleError(f"Appointments can only be cancelled up to {CHANGE_CUTOFF_H} hours before they start. Please contact the clinic.", 409)
        c.execute("UPDATE appointments SET status='cancelled', cancel_reason=%s, updated_at=%s WHERE id=%s", (reason, db.now(), appointment_id))
        db.audit("cancel", "appointment", appointment_id, actor, {"reason": reason}, conn=c)
    return get(appointment_id)


def complete_for_consultation(cid):
    """A doctor finishing the case closes its appointment."""
    with db.connect() as c:
        c.execute("UPDATE appointments SET status='completed', updated_at=%s WHERE consultation_id=%s AND status='booked'", (db.now(), cid))


# ---------------------------------------------------------------------------------------------- closures
def list_blocks(from_now=True):
    q, args = ("SELECT b.*, u.full_name AS doctor_name FROM schedule_blocks b LEFT JOIN users u ON u.id = b.doctor_id"), []
    if from_now:
        q += " WHERE b.end_at > %s"
        args.append(iso(now_utc()))
    q += " ORDER BY b.start_at"
    with db.connect() as c:
        rows = c.execute(q, args).fetchall()
    out = []
    for r in rows:
        st, en = parse(r["start_at"]), parse(r["end_at"])
        whole = local(st).time() == time(0, 0) and local(en).time() == time(0, 0)
        if whole:
            last = local(en - timedelta(seconds=1))
            label = day_label(st) if local(st).date() == last.date() else f"{day_label(st)} – {day_label(last)}"
            label += " · all day"
        else:
            label = f"{day_label(st)} · {t12(st)} – {t12(en)} {TZ_LABEL}"
        out.append({"id": r["id"], "doctor_id": r["doctor_id"], "doctor_name": r["doctor_name"] or "Whole clinic",
                    "reason": r["reason"], "start": iso(st), "end": iso(en), "label": label})
    return out


def add_block(day_from, day_to=None, start_time=None, end_time=None, doctor_id=None, reason=None, actor="admin"):
    """Close whole days (day_from..day_to inclusive) or, with start/end times, part of a single day."""
    try:
        d1 = date.fromisoformat(day_from)
        d2 = date.fromisoformat(day_to) if day_to else d1
    except (TypeError, ValueError):
        raise ScheduleError("Dates must be YYYY-MM-DD")
    if d2 < d1:
        raise ScheduleError("The end date is before the start date")
    if start_time and end_time:
        try:
            t1, t2 = time.fromisoformat(start_time), time.fromisoformat(end_time)
        except ValueError:
            raise ScheduleError("Times must be HH:MM")
        if t2 <= t1:
            raise ScheduleError("The end time must be after the start time")
        s, e = _at(d1, t1), _at(d1, t2)
    else:
        s, e = _at(d1, time(0, 0)), _at(d2 + timedelta(days=1), time(0, 0))
    with db.connect() as c:
        if doctor_id and not c.execute("SELECT 1 FROM users WHERE id=%s AND role='clinician'", (doctor_id,)).fetchone():
            raise ScheduleError("Unknown doctor", 404)
        bid = str(uuid.uuid4())
        c.execute("INSERT INTO schedule_blocks(id, doctor_id, start_at, end_at, reason, created_by, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                  (bid, doctor_id or None, iso(s), iso(e), (reason or "").strip()[:200] or None, actor, db.now()))
        db.audit("create", "schedule_block", bid, actor, {"start": iso(s), "end": iso(e), "doctor": doctor_id}, conn=c)
        # Existing bookings inside the closure are NOT cancelled automatically — the admin decides — but they are reported.
        q = _SELECT + "WHERE a.status = 'booked' AND a.start_at < %s AND a.end_at > %s"
        args = [iso(e), iso(s)]
        if doctor_id:
            q += " AND a.doctor_id = %s"
            args.append(doctor_id)
        clash = [_present(r) for r in c.execute(q + " ORDER BY a.start_at", args).fetchall()]
    return bid, clash


def delete_block(block_id, actor="admin"):
    with db.connect() as c:
        n = c.execute("DELETE FROM schedule_blocks WHERE id = %s", (block_id,)).rowcount
        if n:
            db.audit("delete", "schedule_block", block_id, actor, conn=c)
    if not n:
        raise ScheduleError("Block not found", 404)


# ---------------------------------------------------------------------------------------------- admin views
def list_all(status=None, doctor_id=None, date_from=None, date_to=None, q=None, limit=500):
    """Every appointment, any status, newest start first — the admin's management list.
    date_from/date_to are clinic-time dates (YYYY-MM-DD, inclusive); q matches the patient's name."""
    sql, args = _SELECT + "WHERE 1=1", []
    if status in ("booked", "cancelled", "completed"):
        sql += " AND a.status = %s"
        args.append(status)
    if doctor_id:
        sql += " AND a.doctor_id = %s"
        args.append(doctor_id)
    try:
        if date_from:
            sql += " AND a.start_at >= %s"
            args.append(iso(_at(date.fromisoformat(date_from), time(0, 0))))
        if date_to:
            sql += " AND a.start_at < %s"
            args.append(iso(_at(date.fromisoformat(date_to) + timedelta(days=1), time(0, 0))))
    except ValueError:
        raise ScheduleError("Dates must be YYYY-MM-DD")
    if q:
        sql += " AND p.full_name ILIKE %s"
        args.append(f"%{q.strip()}%")
    sql += " ORDER BY a.start_at DESC LIMIT %s"
    args.append(limit)
    with db.connect() as c:
        return [_present(r) for r in c.execute(sql, args).fetchall()]


def needs_time():
    """Paid consultations still waiting for the doctor that have no booked appointment (older cases, or a cancelled one)."""
    with db.connect() as c:
        rows = c.execute(
            "SELECT c.id, c.patient_id, c.paid_at, c.created_at, p.full_name AS patient_name FROM consultations c "
            "JOIN patients p ON p.id = c.patient_id WHERE c.status = 'awaiting_doctor' "
            "AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.consultation_id = c.id AND a.status = 'booked') ORDER BY c.paid_at").fetchall()
    return [{"consultation_id": r["id"], "patient_id": r["patient_id"], "patient_name": r["patient_name"],
             "paid_at": r["paid_at"], "paid_label": f"{day_label(parse(r['paid_at']))} {t12(parse(r['paid_at']))}" if r["paid_at"] else None} for r in rows]


def utilization(date_str=None, now=None):
    """Capacity vs bookings for the Mon-Fri week containing date_str, per day and per doctor — what the admin watches."""
    now = now or now_utc()
    try:
        d0 = date.fromisoformat(date_str) if date_str else local(now).date()
    except ValueError:
        raise ScheduleError("date must be YYYY-MM-DD")
    mon = monday_of(d0)
    lo, hi = _at(mon, time(0, 0)).astimezone(UTC), _at(mon + timedelta(days=7), time(0, 0)).astimezone(UTC)
    with db.connect() as c:
        doctors = _doctors(c)
        booked = c.execute("SELECT doctor_id, start_at FROM appointments WHERE status IN ('booked','completed') AND start_at >= %s AND start_at < %s",
                           (iso(lo), iso(hi))).fetchall()
        blocks = c.execute("SELECT doctor_id, start_at, end_at, reason FROM schedule_blocks WHERE end_at > %s AND start_at < %s", (iso(lo), iso(hi))).fetchall()
    days = []
    for k in range(5):
        d = mon + timedelta(days=k)
        slots = [i for i in day_items(d) if i["kind"] == "slot"]
        per = []
        for dr in doctors:
            cap = sum(1 for sl in slots if not _block_covering(blocks, sl["start"].astimezone(UTC), sl["end"].astimezone(UTC), dr["id"]))
            used = sum(1 for b in booked if b["doctor_id"] == dr["id"] and local(parse(b["start_at"])).date() == d)
            per.append({"doctor_id": dr["id"], "doctor_name": dr["full_name"], "capacity": cap, "booked": used})
        days.append({"date": d.isoformat(), "label": f"{d:%a} {d.day} {d:%b}", "doctors": per,
                     "capacity": sum(x["capacity"] for x in per), "booked": sum(x["booked"] for x in per)})
    return {"week_start": mon.isoformat(), "days": days, "tz_label": TZ_LABEL, "slots_per_doctor_day": len([i for i in day_items(mon) if i["kind"] == "slot"]),
            "prev": (mon - timedelta(days=7)).isoformat(), "next": (mon + timedelta(days=7)).isoformat()}
