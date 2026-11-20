import httpx, base64, glob, json, re
from datetime import datetime, timedelta, timezone
import database as db
B="http://127.0.0.1:8010"
fails=[]
def ok(c,m):
    print(("PASS " if c else "FAIL ")+m); 
    if not c: fails.append(m)
def login(e,p):
    c=httpx.Client(timeout=120); r=c.post(B+"/api/auth/login",json={"email":e,"password":p}); assert r.status_code==200,(e,r.text); return c
face=sorted(glob.glob("facial-skin-images-datasets/skin_type_classification_dataset/train/*/*.jpg"))[3]
IMG="data:image/jpeg;base64,"+base64.b64encode(open(face,"rb").read()).decode()
anon=httpx.Client(timeout=60); doc=login("doctor@clarity.local","Doctor12345"); adm=login("admin@clarity.local","Admin12345")
def mk(name):
    c=httpx.Client(timeout=120); r=c.post(B+"/api/auth/signup",json={"full_name":name,"email":f"zz.{name.split()[-1].lower()}@example.com","password":"TestPass123!","age":30,"sex":"Female"}); assert r.status_code==200,r.text; return c,r.json()
p1,u1=mk("ZZ Journey One"); p2,u2=mk("ZZ Journey Two")
ok(re.fullmatch(r"PT-\d{5}",u1["user_code"]) and u1["user_code"]!=u2["user_code"], f"unique patient IDs {u1['user_code']} / {u2['user_code']}, record no. {u1.get('mrn')}")
ok(doc.get(B+"/api/auth/me").json()["user_code"].startswith("DR-") and adm.get(B+"/api/auth/me").json()["user_code"].startswith("AD-"), "doctor DR-, admin AD- IDs")

# ---------- RBAC matrix
def code(c,m,u,**k): return getattr(c,m)(B+u,**k).status_code
rows=[("analyze",'post',"/api/analyze",{"json":{"image_base64":IMG}}),("patients list",'get',"/api/patients",{}),("scans list",'get',"/api/scans",{}),
      ("dashboard stats",'get',"/api/dashboard/stats",{}),("hospital metrics",'get',"/api/hospital-metrics",{}),("learning status",'get',"/api/learning/status",{}),
      ("admin users",'get',"/api/admin/users",{}),("admin appointments",'get',"/api/admin/appointments",{}),("doctors referral",'get',"/api/doctors",{}),
      ("consultations list",'get',"/api/consultations",{}),("pending followups",'get',"/api/consultations/pending-followups",{}),("journey",'get',"/api/me/journey",{})]
expect={"analyze":(401,200,403,200),"patients list":(401,403,403,200),"scans list":(401,403,403,200),"dashboard stats":(401,403,403,200),"hospital metrics":(401,403,403,200),
        "learning status":(401,403,403,200),"admin users":(401,403,403,200),"admin appointments":(401,403,403,200),"doctors referral":(401,403,403,200),
        "consultations list":(401,200,200,200),"pending followups":(401,403,200,200),"journey":(401,200,403,403)}
for name,m,u,k in rows:
    if name=="analyze": continue   # exercised below (it runs a model)
    got=(code(anon,m,u,**k),code(p1,m,u,**k),code(doc,m,u,**k),code(adm,m,u,**k)); ok(got==expect[name], f"{name:20s} anon/patient/doctor/admin -> {got}")
r=anon.post(B+"/api/analyze",json={"image_base64":IMG}); ok(r.status_code==401,"analyze anonymous -> 401 (drag&drop is not public)")
r=doc.post(B+"/api/analyze",json={"image_base64":IMG}); ok(r.status_code==403,"analyze as doctor -> 403")

# ---------- journey: first scan, one scan only
j=p1.get(B+"/api/me/journey").json(); ok(j["stage"]=="new" and j["can_start_new"],"new patient: stage 'new'")
r=p1.post(B+"/api/analyze",json={"image_base64":IMG}); ok(r.status_code==200,f"first scan -> {r.status_code}"); sid=r.json()["scan_id"]
ok(r.json()["learning"].get("enabled") is False,"result says learning is off")
j=p1.get(B+"/api/me/journey").json(); ok(j["stage"]=="report" and j["report"]["scan_id"]==sid and j["report"]["routine"],"journey resumes at the report with the same scan")
r=p1.post(B+"/api/analyze",json={"image_base64":IMG}); ok(r.status_code==403,f"second scan before consultation blocked -> {r.status_code} ({r.json().get('detail')})")
c1=p1.post(B+"/api/consultations",json={"scan_id":sid}).json(); c1b=p1.post(B+"/api/consultations",json={"scan_id":sid}).json(); ok(c1["id"]==c1b["id"],"creating the consultation twice returns the same one")
j=p1.get(B+"/api/me/journey").json(); ok(j["stage"]=="payment","stage 'payment'")
ok(p1.post(B+"/api/analyze",json={"image_base64":IMG}).status_code==403,"scan blocked while consultation is open")
ok(p2.get(B+f"/api/consultations/{c1['id']}").status_code==403,"other patient cannot read this consultation")
ok(p2.get(B+f"/api/scans/{sid}").status_code==403 and p2.get(B+f"/api/scans/{sid}/image").status_code==403,"other patient cannot read this scan/photo")
cons_list=p1.get(B+"/api/consultations?patient_id=someone-else").json()["consultations"]; ok(all(c["patient_id"]==u1["patient_id"] for c in cons_list),"patient list is always limited to their own")
# book + pay
w=p1.get(B+"/api/schedule/week").json(); slot=[it for d in w["days"] for it in d["items"] if it["kind"]=="slot" and it["status"]=="available"][-1]
r=p1.post(B+f"/api/consultations/{c1['id']}/pay",json={"slot_start":slot["start"]}); ok(r.status_code==200,f"pay + appointment -> {r.status_code}")
ok(p1.get(B+"/api/me/journey").json()["stage"]=="pending","stage 'pending'")
ok(p1.post(B+"/api/analyze",json={"image_base64":IMG}).status_code==403,"scan blocked while doctor review is pending")
# doctor completes
r=doc.patch(B+f"/api/consultations/{c1['id']}",json={"symptoms":"s","diagnosis":"d","cure":"c","prescription":"p","followup_days":7,"verified_skin_type":"oily","verified_severity":{"acne_severity":2.0}})
ok(r.status_code==200,f"doctor completes -> {r.status_code}"); print("   added_to_memory:",r.json().get("added_to_memory"),"| retrain:",r.json().get("retrain"))
ok(r.json().get("added_to_memory") is False,"doctor verdict is NOT fed to the model (learning off)")
ok(adm.post(B+"/api/learning/retrain",json={}).status_code==403,"manual retrain refused (403)")
st=adm.get(B+"/api/learning/status").json(); ok(st["learning_enabled"] is False and st["memory_size"]==0,"learning status: off, memory 0")
j=p1.get(B+"/api/me/journey").json(); cps=j["checkpoints"]; ok(j["stage"]=="completed" and [c["day"] for c in cps]==[15,30,45] and cps[0]["status"]=="upcoming", f"completed; check-ins {[(c['day'],c['status']) for c in cps]}; day 15 opens {cps[0]['opens_label']}")
con=db.get_consultation(c1["id"]); ok(con["followup_days"]==15,"doctor's followup_days is ignored: first check-in is day 15")
r=p1.post(B+"/api/analyze",json={"image_base64":IMG,"consultation_id":c1["id"]}); ok(r.status_code==403,f"day-15 scan too early -> {r.status_code} ({r.json().get('detail')})")
r=p1.post(B+"/api/analyze",json={"image_base64":IMG}); ok(r.status_code==403,f"new baseline scan during the programme -> {r.status_code}")
ok(doc.get(B+"/api/consultations/pending-followups").json()["consultations"]==[] or all(c["id"]!=c1["id"] for c in doc.get(B+"/api/consultations/pending-followups").json()["consultations"]),"doctor follow-up list: not due yet")

def shift(days):
    ts=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat(timespec="seconds")
    with db.connect() as cx: cx.execute("update consultations set completed_at=%s where id=%s",(ts,c1["id"]))
# day 15 (calendar): completed 15 days ago -> opens at IST midnight of that date, so already open
shift(15); cps=p1.get(B+"/api/me/journey").json()["checkpoints"]; ok(cps[0]["status"]=="available" and cps[1]["status"]=="upcoming",f"15 days later: {[(c['day'],c['status']) for c in cps]}")
ok(any(c["id"]==c1["id"] for c in doc.get(B+"/api/consultations/pending-followups").json()["consultations"]),"doctor's 'follow-ups due' lists the patient")
r=p1.post(B+"/api/analyze",json={"image_base64":IMG,"consultation_id":c1["id"]}); ok(r.status_code==200 and r.json().get("checkpoint_day")==15 and r.json().get("followup_comparison"),f"day-15 scan accepted -> {r.status_code}, comparison vs first scan: {bool(r.json().get('followup_comparison'))}")
r=p1.post(B+"/api/analyze",json={"image_base64":IMG,"consultation_id":c1["id"]}); ok(r.status_code==403,"second scan in the same window refused")
cps=p1.get(B+"/api/me/journey").json()["checkpoints"]; ok(cps[0]["status"]=="done" and cps[0].get("comparison"),"journey shows day 15 done with comparison")
# window passes: day 31 -> 15 missed(if not done)/done, 30 available
shift(31); cps=p1.get(B+"/api/me/journey").json()["checkpoints"]; ok([c["status"] for c in cps]==["done","available","upcoming"],f"31 days: {[(c['day'],c['status']) for c in cps]}")
r=p1.post(B+"/api/analyze",json={"image_base64":IMG,"consultation_id":c1["id"]}); ok(r.status_code==200 and r.json()["checkpoint_day"]==30,"day-30 scan accepted")
shift(47); cps=p1.get(B+"/api/me/journey").json()["checkpoints"]; ok([c["status"] for c in cps]==["done","done","available"],f"47 days: {[(c['day'],c['status']) for c in cps]}")
r=p1.post(B+"/api/analyze",json={"image_base64":IMG,"consultation_id":c1["id"]}); ok(r.status_code==200 and r.json()["checkpoint_day"]==45,"day-45 scan accepted")
j=p1.get(B+"/api/me/journey").json(); ok(j["can_start_new"] and j["stage"]=="completed","after day 45 the cycle is over: a new assessment may start")
ok(any(c["id"]==c1["id"] for c in doc.get(B+"/api/consultations?status=completed").json()["consultations"]),"doctor history lists the case")
det=doc.get(B+f"/api/consultations/{c1['id']}").json(); ok(len([c for c in det["checkpoints"] if c.get("comparison")])==3,"doctor's case view carries all 3 comparisons")
r=p1.post(B+"/api/analyze",json={"image_base64":IMG}); ok(r.status_code==200,f"new baseline scan after the programme -> {r.status_code}")
ok(p1.get(B+"/api/me/journey").json()["stage"]=="report","new cycle starts at 'report'")
# missed window: patient two skips day 15
sid2=p2.post(B+"/api/analyze",json={"image_base64":IMG}).json()["scan_id"]; c2=p2.post(B+"/api/consultations",json={"scan_id":sid2}).json()
w=p2.get(B+"/api/schedule/week").json(); slot=[it for d in w["days"] for it in d["items"] if it["kind"]=="slot" and it["status"]=="available"][-3]
p2.post(B+f"/api/consultations/{c2['id']}/pay",json={"slot_start":slot["start"]}); doc.patch(B+f"/api/consultations/{c2['id']}",json={"symptoms":"s","diagnosis":"d","cure":"c","prescription":"p"})
ts=(datetime.now(timezone.utc)-timedelta(days=35)).isoformat(timespec="seconds")
with db.connect() as cx: cx.execute("update consultations set completed_at=%s where id=%s",(ts,c2["id"]))
cps=p2.get(B+"/api/me/journey").json()["checkpoints"]; ok([c["status"] for c in cps]==["missed","available","upcoming"],f"skipped day 15 -> {[(c['day'],c['status']) for c in cps]}")
# admin dashboard scan (drag & drop) still works for admins
r=adm.post(B+"/api/analyze",json={"image_base64":IMG,"patient_id":u2["patient_id"]}); ok(r.status_code==200,f"admin scan from the dashboard -> {r.status_code}")
r=adm.post(B+"/api/analyze",json={"image_base64":IMG}); ok(r.status_code==200,"admin anonymous demo scan -> 200")
# cleanup
with db.connect() as cx:
    pids=[r['id'] for r in cx.execute("select id from patients where full_name like 'ZZ %'").fetchall()]
    # admin-made anonymous demo scans have no patient: remove the ones created just now
    cx.execute("delete from scans where patient_id is null and captured_at > %s",((datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(),))
    for p in pids: cx.execute("delete from consultations where patient_id=%s",(p,))
for p in pids: db.delete_patient(p,actor="test")
with db.connect() as cx:
    for u in cx.execute("select id from users where email like 'zz.%%@example.com'").fetchall(): db.delete_user(u['id'])
print("\nFAILED:",fails if fails else "none")
