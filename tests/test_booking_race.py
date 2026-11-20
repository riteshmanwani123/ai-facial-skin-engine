import httpx, threading
import database as db
B="http://127.0.0.1:8010"
with db.connect() as cx: scan=cx.execute("select id from scans limit 1").fetchone()['id']
cl=[];cons=[];pids=[]
for i in range(6):
    c=httpx.Client(timeout=60); assert c.post(B+"/api/auth/signup",json={"full_name":f"ZZ Race {i}","email":f"zz.race{i}@example.com","password":"TestPass123!"}).status_code==200
    with db.connect() as cx: pid=cx.execute("select id from patients where full_name=%s",(f"ZZ Race {i}",)).fetchone()['id']
    pids.append(pid); cons.append(db.create_consultation(pid,scan,fee_usd=99.0,ai_suggestions={})['id']); cl.append(c)
w=cl[0].get(B+"/api/schedule/week?date=2026-10-26").json()
slot=[it for d in w['days'] for it in d['items'] if it['kind']=='slot' and it['status']=='available' and it['time']=='11:00'][0]
res=[None]*6
def go(i): res[i]=cl[i].post(B+f"/api/consultations/{cons[i]}/pay",json={"slot_start":slot['start']}).status_code
ts=[threading.Thread(target=go,args=(i,)) for i in range(6)]; [t.start() for t in ts]; [t.join() for t in ts]
print("6 patients race for ONE doctor-slot:",sorted(res), "-> exactly one 200:", res.count(200)==1)
with db.connect() as cx:
    print("booked rows for that slot:",cx.execute("select count(*) n from appointments where start_at=%s and status='booked'",(slot['start'],)).fetchone()['n'],
          "| paid consultations among them:",cx.execute("select count(*) n from consultations where id = ANY(%s) and status='awaiting_doctor'",(cons,)).fetchone()['n'])
winner=res.index(200); a=cl[winner].get(B+f"/api/consultations/{cons[winner]}").json()['appointment']
r=cl[winner].post(B+f"/api/appointments/{a['id']}/cancel"); print("winner cancels:",r.status_code,r.json().get('status'))
w=cl[0].get(B+"/api/schedule/week?date=2026-10-26").json(); it=[it for d in w['days'] for it in d['items'] if it.get('start')==slot['start']][0]; print("slot after cancel:",it['status'])
with db.connect() as cx:
    for pid in pids: cx.execute("delete from consultations where patient_id=%s",(pid,))
for pid in pids: db.delete_patient(pid,actor="test")
with db.connect() as cx:
    for u in cx.execute("select id from users where email like 'zz.race%%@example.com'").fetchall(): db.delete_user(u['id'])
    print("left:",cx.execute("select count(*) n from appointments").fetchone()['n'])
