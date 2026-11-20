import httpx, json, sys
from datetime import datetime, timedelta, date, time, timezone
import database as db, scheduling as S, auth
B="http://127.0.0.1:8010"
ok=lambda c,m: print(("PASS " if c else "FAIL ")+m)

cfg=S.config(); ok(cfg["slots_per_hour"]==3 and cfg["slots_per_day"]==23, f"config: 3/hour, {cfg['slots_per_day']} bookable/day (27 minus 4 break slots)")
d=date(2026,10,14)  # Wednesday
items=S.day_items(d); slots=[i for i in items if i['kind']=='slot']; brks=[i for i in items if i['kind']=='break']
ok(len(slots)==23 and len(brks)==2, f"day template: {len(slots)} slots + {len(brks)} breaks")
ok(slots[0]['start'].strftime('%H:%M')=='10:00' and slots[-1]['end'].strftime('%H:%M')=='19:00', "first slot 10:00, last ends 19:00")
ok(not any(13*60<=s['start'].hour*60+s['start'].minute<14*60 for s in slots), "no slot inside the 13:00-14:00 lunch")
ok(S.day_items(date(2026,10,17))==[] and S.day_items(date(2026,10,18))==[], "Saturday and Sunday are closed")

# users: one extra temp doctor to test assignment
with db.connect() as c:
    docs=[r['id'] for r in c.execute("select id from users where role='clinician'").fetchall()]
tmp_doc=db.create_user("zz.doc2@example.com", auth.hash_password("TestPass123!"), "ZZ Doc Two", "clinician")
# two throwaway patients + consultations (need scans): reuse an existing scan row
with db.connect() as c:
    scan=c.execute("select id from scans limit 1").fetchone()['id']
pats=[db.create_patient(f"ZZ Sched {i}", actor="test") for i in (1,2,3)]
cons=[db.create_consultation(p['id'], scan, fee_usd=99.0, ai_suggestions={}) for p in pats]
for cn in cons: db.mark_consultation_paid(cn['id'])

now=datetime(2026,10,14,4,30,tzinfo=timezone.utc)  # 10:00 IST Wed
nxt=local=None
def slot_at(day, hhmm):  # utc iso of a clinic-time slot
    h,m=map(int,hhmm.split(':')); return S.iso(S._at(day, time(h,m)))
mon_next=date(2026,10,19)
t=slot_at(mon_next,"10:20")
a1=S.book(cons[0]['id'],pats[0]['id'],t,now=now); ok(a1['status']=='booked' and a1['start_label']=='10:20 AM', f"book Mon 10:20 -> {a1['day_label']} {a1['time_label']} with {a1['doctor_name']}")
a2=S.book(cons[1]['id'],pats[1]['id'],t,now=now); ok(a2['doctor_id']!=a1['doctor_id'], "same slot, 2nd patient gets a DIFFERENT doctor")
try: S.book(cons[2]['id'],pats[2]['id'],t,now=now); ok(False,"3rd booking in a full slot should fail")
except S.ScheduleError as e: ok(e.status==409, f"3rd booking refused: {e}")
w=S.week("2026-10-19",now=now); mon=[x for x in w['days'] if x['weekday']=='Mon'][0]; it=[i for i in mon['items'] if i['kind']=='slot' and i['time']=='10:20'][0]
ok(it['status']=='full', "slot shows as full once both doctors are booked")
ok(len(w['days'])==5 and w['days'][0]['weekday']=='Mon' and w['days'][4]['weekday']=='Fri', "week view is Mon..Fri")
ok(sum(1 for i in mon['items'] if i['kind']=='break')==2, "breaks are included in the day's rows")

for label,when,expect in [("Saturday",slot_at(date(2026,10,17),"11:00"),400),("lunch slot",slot_at(mon_next,"13:20"),400),("before opening",slot_at(mon_next,"09:40"),400),
                          ("off the 20-min grid",slot_at(mon_next,"10:30"),400),("after closing",slot_at(mon_next,"19:00"),400),
                          ("within lead time",slot_at(date(2026,10,14),"10:20"),400),("beyond horizon",slot_at(date(2026,12,14),"10:00"),400)]:
    try: S.book(cons[2]['id'],pats[2]['id'],when,now=now); ok(False,f"{label} should be refused")
    except S.ScheduleError as e: ok(e.status==expect, f"{label} refused ({e})")

# reschedule
new_t=slot_at(date(2026,10,20),"15:40")
r=S.book(cons[0]['id'],pats[0]['id'],new_t,now=now)
old=S.get(a1['id']); ok(old['status']=='cancelled' and r['status']=='booked', "reschedule cancels the old appointment and books the new one")
w=S.week("2026-10-19",now=now); mon=[x for x in w['days'] if x['weekday']=='Mon'][0]; it=[i for i in mon['items'] if i['kind']=='slot' and i['time']=='10:20'][0]
ok(it['status']=='available', "old slot is free again")
# cutoff
near=datetime(2026,10,20,9,30,tzinfo=timezone.utc)   # 15:00 IST on the day -> 40 minutes before 15:40
try: S.cancel(r['id'],now=near); ok(False,"cancel inside cutoff should fail")
except S.ScheduleError as e: ok(e.status==409, f"patient cancel inside the 2h cutoff refused ({e})")
sc=S.cancel(r['id'],staff=True,now=near); ok(sc['status']=='cancelled', "staff can cancel inside the cutoff")
# blocks
bid,_=S.add_block("2026-10-21",actor="test",reason="Test holiday")
w=S.week("2026-10-19",now=now); wed=[x for x in w['days'] if x['weekday']=='Wed'][0]
ok(wed['available']==0 and wed['closed_reason']=="Test holiday", "a clinic-wide block closes the day")
try: S.book(cons[2]['id'],pats[2]['id'],slot_at(date(2026,10,21),"11:00"),now=now); ok(False,"booking a closed day should fail")
except S.ScheduleError as e: ok(e.status==409, f"closed day refused ({e})")
S.delete_block(bid,actor="test")
bid,_=S.add_block("2026-10-22",start_time="10:00",end_time="12:00",doctor_id=tmp_doc['id'],actor="test",reason="Leave")
t2=slot_at(date(2026,10,22),"10:20")
x=S.book(cons[2]['id'],pats[2]['id'],t2,now=now); ok(x['doctor_id']!=tmp_doc['id'], "doctor on leave is never assigned")
S.delete_block(bid,actor="test")
# cleanup
with db.connect() as c:
    for p in pats: c.execute("delete from consultations where patient_id=%s",(p['id'],))
for p in pats: db.delete_patient(p['id'],actor="test")
db.delete_user(tmp_doc['id'])
with db.connect() as c: print("appointments left:", c.execute("select count(*) n from appointments").fetchone()['n'])
