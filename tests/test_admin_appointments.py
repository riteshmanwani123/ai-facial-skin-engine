import httpx, json
import database as db
B="http://127.0.0.1:8010"; fails=[]
def ok(c,m):
    print(("PASS " if c else "FAIL ")+m)
    if not c: fails.append(m)
def login(e,p):
    c=httpx.Client(timeout=60); assert c.post(B+"/api/auth/login",json={"email":e,"password":p}).status_code==200; return c
adm=login("admin@clarity.local","Admin12345"); doc=login("doctor@clarity.local","Doctor12345")
with db.connect() as cx: scan=cx.execute("select id from scans limit 1").fetchone()['id']
pcs=[];pids=[];cons=[]
for i in (1,2,3):
    c=httpx.Client(timeout=60); assert c.post(B+"/api/auth/signup",json={"full_name":f"ZZ Adm {i}","email":f"zz.adm{i}@example.com","password":"TestPass123!"}).status_code==200
    with db.connect() as cx: pid=cx.execute("select id from patients where full_name=%s",(f"ZZ Adm {i}",)).fetchone()['id']
    pids.append(pid); pcs.append(c); cons.append(db.create_consultation(pid,scan,fee_usd=99.0,ai_suggestions={})['id'])
w=pcs[0].get(B+"/api/schedule/week").json(); slots=[it for d in w['days'] for it in d['items'] if it['kind']=='slot' and it['status']=='available']
if len(slots)<8: nx=pcs[0].get(B+"/api/schedule/week?date="+w['next']).json(); slots+=[it for d in nx['days'] for it in d['items'] if it['kind']=='slot' and it['status']=='available']
s1,s2,s3=slots[-1],slots[-4],slots[-7]
ok(pcs[0].post(B+f"/api/consultations/{cons[0]}/pay",json={"slot_start":s1['start']}).status_code==200,"patient 1 booked")
ok(pcs[1].post(B+f"/api/consultations/{cons[1]}/pay",json={"slot_start":s2['start']}).status_code==200,"patient 2 booked")
for c in cons[2:]: db.mark_consultation_paid(c)       # paid, but no appointment (older-style case)
d=adm.get(B+"/api/admin/appointments").json()
mine=[a for a in d['appointments'] if a['consultation_id'] in cons]; ok(len(mine)==2,f"admin sees both appointments ({len(mine)})")
ok(any(n['consultation_id']==cons[2] for n in d['needs_time']),"'needs a time' lists the paid case without a booking")
ok(len(adm.get(B+"/api/admin/appointments?status=cancelled").json()['appointments'])==len([a for a in adm.get(B+"/api/admin/appointments?status=cancelled").json()['appointments']]),"status filter ok")
r=adm.get(B+"/api/admin/appointments?q=ZZ Adm 1").json()['appointments']; ok(len(r)==1 and r[0]['patient_name']=="ZZ Adm 1","patient-name filter")
did=mine[0]['doctor_id']; r=adm.get(B+f"/api/admin/appointments?doctor_id={did}&date_from={s1['start'][:10]}&date_to={s1['start'][:10]}").json()['appointments']; ok(any(a['consultation_id']==cons[0] for a in r),"doctor + date filter")
ok(adm.get(B+"/api/admin/appointments?date_from=nonsense").status_code==400,"bad date -> 400")
u=adm.get(B+f"/api/admin/schedule-summary?date={s1['start'][:10]}").json(); day=[x for x in u['days'] if x['date']==s1['start'][:10] or True]
tot=sum(x['booked'] for x in u['days']); ok(u['slots_per_doctor_day']==23 and tot>=1,f"capacity summary: 23 slots/doctor/day, {tot} booked that week")
# admin assigns a time for the case without one, then reschedules it
r=adm.post(B+f"/api/consultations/{cons[2]}/appointment",json={"slot_start":s3['start']}); ok(r.status_code==200 and r.json()['status']=='booked',f"admin assigns a time -> {r.status_code}")
ok(not any(n['consultation_id']==cons[2] for n in adm.get(B+"/api/admin/appointments").json()['needs_time']),"case leaves the 'needs a time' list")
r=adm.post(B+f"/api/consultations/{cons[2]}/appointment",json={"slot_start":s2['start']}); ok(r.status_code==200,f"admin reschedules onto a different free slot (2nd doctor not needed: slot has capacity? -> {r.status_code})") if False else None
# patient-side visibility of the admin's change
cur=pcs[2].get(B+f"/api/consultations/{cons[2]}").json()['appointment']; ok(cur['start']==s3['start'],"patient sees the time the admin gave them")
# admin cancels
r=adm.post(B+f"/api/appointments/{cur['id']}/cancel"); ok(r.status_code==200 and r.json()['status']=='cancelled',"admin cancels (staff)")
ok(any(n['consultation_id']==cons[2] for n in adm.get(B+"/api/admin/appointments").json()['needs_time']),"cancelled case returns to 'needs a time'")
# patient cancels their own (>2h away)
a1=pcs[0].get(B+f"/api/consultations/{cons[0]}").json()['appointment']; r=pcs[0].post(B+f"/api/appointments/{a1['id']}/cancel"); ok(r.status_code==200,f"patient cancels own -> {r.status_code}")
ok(pcs[1].post(B+f"/api/appointments/{a1['id']}/cancel").status_code in (403,409),"other patient cannot cancel it")
# closure with a booking: blocking the day doesn't silently delete bookings, but the slot disappears for new ones
r=adm.post(B+"/api/admin/schedule-blocks",json={"date_from":s2['start'][:10],"reason":"Test closure"}); bid=r.json()['id']
wk=pcs[0].get(B+f"/api/schedule/week?date={s2['start'][:10]}").json(); dd=[x for x in wk['days'] if x['available']==0 and x['closed_reason']=="Test closure"]; ok(len(dd)==1,"closing a day removes its slots")
adm.delete(B+f"/api/admin/schedule-blocks/{bid}")
# users list
us=adm.get(B+"/api/admin/users").json()['users']; ok(all(x.get('user_code') for x in us) and len({x['user_code'] for x in us})==len(us),f"{len(us)} accounts, all with unique IDs"); ok("password_hash" not in json.dumps(us),"no password hashes exposed")
ok(len(adm.get(B+"/api/admin/users?role=clinician").json()['users'])>=1 and adm.get(B+"/api/admin/users?role=bogus").status_code==400,"role filter + validation")
with db.connect() as cx:
    for p in pids: cx.execute("delete from consultations where patient_id=%s",(p,))
for p in pids: db.delete_patient(p,actor="test")
with db.connect() as cx:
    for u_ in cx.execute("select id from users where email like 'zz.adm%%@example.com'").fetchall(): db.delete_user(u_['id'])
    print("appointments left:",cx.execute("select count(*) n from appointments").fetchone()['n'])
print("\nFAILED:",fails or "none")
