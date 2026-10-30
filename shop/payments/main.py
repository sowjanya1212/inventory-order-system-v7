import os, sqlite3, uuid, httpx
from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from events import publish
KEY = os.getenv("INTERNAL_KEY", "internal"); ORDERS = os.getenv("ORDERS_URL", "http://127.0.0.1:8004")
app = FastAPI(title="Payment Service (simulated, no card data stored)")
def U(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    if not x_user_id: raise HTTPException(401, "Login required")
    return {"sub": x_user_id, "role": x_user_role}
def H(u): return {"X-User-Id": u["sub"], "X-User-Role": u["role"]}
def db():
    c = sqlite3.connect("payments.db"); c.row_factory = sqlite3.Row; return c
with db() as c:
    c.execute("create table if not exists payments(id integer primary key, reference text, order_id int, user_id text, amount real, status text, idem text, created text default current_timestamp)")
class P(BaseModel):
    order_id: int; simulate: str = Field(pattern="^(success|failure|pending)$")
STATUS = {"success": "Success", "failure": "Failed", "pending": "Initiated"}
@app.get("/health")
def health(): return {"status": "ok"}
@app.post("/payments", status_code=201)
def pay(b: P, u: dict = Depends(U), idempotency_key: str = Header(None)):
    c = db()
    if idempotency_key:
        r = c.execute("select * from payments where idem=? and user_id=?", (idempotency_key, u["sub"])).fetchone()
        if r: return dict(r)
    r = c.execute("select * from payments where order_id=? and status='Success'", (b.order_id,)).fetchone()
    if r: return dict(r)  # duplicate request: never charge twice
    try: o = httpx.get(f"{ORDERS}/orders/{b.order_id}", headers=H(u), timeout=5)
    except httpx.HTTPError: raise HTTPException(503, "Order service unavailable")
    if o.status_code != 200: raise HTTPException(404, "Order not found")
    o = o.json()
    if o["status"] != "Pending": raise HTTPException(409, f"Order is {o['status']}, payment not possible")
    with c: cur = c.execute("insert into payments(reference,order_id,user_id,amount,status,idem) values(?,?,?,?,?,?)",
        ("PAY-" + uuid.uuid4().hex[:10].upper(), b.order_id, o["user_id"], o["total"], STATUS[b.simulate], idempotency_key))
    if b.simulate == "success":
        try: httpx.post(f"{ORDERS}/orders/{b.order_id}/paid", headers={"X-Internal-Key": KEY}, timeout=5).raise_for_status()
        except httpx.HTTPError:
            with c: c.execute("update payments set status='Initiated' where id=?", (cur.lastrowid,))
            raise HTTPException(503, "Could not confirm the order, please retry")
    elif b.simulate == "failure": publish("payment.failed", user_id=o["user_id"], order_number=o["number"])
    return dict(c.execute("select * from payments where id=?", (cur.lastrowid,)).fetchone())
def own(pid, u):
    r = db().execute("select * from payments where id=?", (pid,)).fetchone()
    if not r or (r["user_id"] != u["sub"] and u["role"] != "admin"): raise HTTPException(404, "Payment not found")
    return r
@app.get("/payments/{pid}")
def get(pid: int, u: dict = Depends(U)): return dict(own(pid, u))
@app.post("/payments/{pid}/cancel")
def cancel(pid: int, u: dict = Depends(U)):
    r = own(pid, u)
    if r["status"] != "Initiated": raise HTTPException(400, f"Cannot cancel a {r['status']} payment")
    with db() as c: c.execute("update payments set status='Cancelled' where id=?", (pid,))
    return {"id": pid, "status": "Cancelled"}

@app.post("/payments/refund-order")
def refund(b: dict, x_internal_key: str = Header(None)):
    if x_internal_key != KEY: raise HTTPException(403, "Internal only")
    with db() as c: n = c.execute("update payments set status='Refunded' where order_id=? and status='Success'", (b["order_id"],)).rowcount
    return {"refunded": n}  # second call returns 0: no duplicate refunds
