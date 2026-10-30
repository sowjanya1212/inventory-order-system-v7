import os, sqlite3, json, jwt, httpx
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
SECRET = os.getenv("JWT_SECRET", "dev-secret")
ORDERS = os.getenv("ORDERS_URL", "http://127.0.0.1:8004"); INVENTORY = os.getenv("INVENTORY_URL", "http://127.0.0.1:8003")
PAYMENTS = os.getenv("PAYMENTS_URL", "http://127.0.0.1:8008")
app = FastAPI(title="Return & Refund Service")

from fastapi import Depends
def U(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    """Identity is set by the gateway after it checks the login token once."""
    if not x_user_id: raise HTTPException(401, "Login required")
    return {"sub": x_user_id, "role": x_user_role}
def H(u): return {"X-User-Id": u["sub"], "X-User-Role": u["role"]}
FLOW = {"Requested": ["Approved", "Rejected"], "Approved": ["Received"], "Received": ["Refunded"]}
def db():
    c = sqlite3.connect("returns.db"); c.row_factory = sqlite3.Row; return c
with db() as c:
    c.execute("create table if not exists returns(id integer primary key, order_id int, user_id text, reason text, status text, history text, created text default current_timestamp)")
class RI(BaseModel):
    product_id: int; qty: int = Field(gt=0)
class R(BaseModel):
    order_id: int; reason: str = Field(min_length=3); items: list[RI] = []  # empty = whole order
class S(BaseModel):
    status: str
@app.get("/health")
def health(): return {"status": "ok"}
@app.post("/returns", status_code=201)
def create(b: R, u: dict = Depends(U)):
    u = u
    try: r = httpx.get(f"{ORDERS}/orders/{b.order_id}", headers=H(u), timeout=5)
    except httpx.HTTPError: raise HTTPException(503, "Order service unavailable")
    if r.status_code != 200: raise HTTPException(404, "Order not found")
    if r.json()["status"] != "Delivered": raise HTTPException(400, "Only delivered orders can be returned")
    ordered = {i["product_id"]: i["qty"] for i in r.json()["items"]}
    sel = [i.model_dump() for i in b.items] or [{"product_id": k, "qty": v} for k, v in ordered.items()]
    for i in sel:
        if i["qty"] > ordered.get(i["product_id"], 0): raise HTTPException(400, "Return quantity exceeds ordered quantity")
    c = db()
    if c.execute("select 1 from returns where order_id=? and status!='Rejected'", (b.order_id,)).fetchone(): raise HTTPException(409, "Return already requested")
    with c: cur = c.execute("insert into returns(order_id,user_id,reason,status,history,items) values(?,?,?,?,?,?)", (b.order_id, u["sub"], b.reason, "Requested", "Requested", json.dumps(sel)))
    return {"id": cur.lastrowid, "status": "Requested"}
@app.get("/returns/my-returns")
def mine(u: dict = Depends(U)):
    return [dict(r) for r in db().execute("select * from returns where user_id=? order by id desc", (u["sub"],))]
@app.get("/returns")
def all_(u: dict = Depends(U)):
    if u["role"] != "admin": raise HTTPException(403, "Admin only")
    return [dict(r) for r in db().execute("select * from returns order by id desc")]
@app.get("/returns/{rid}")
def get(rid: int, u: dict = Depends(U)):
    u = u; r = db().execute("select * from returns where id=?", (rid,)).fetchone()
    if not r or (r["user_id"] != u["sub"] and u["role"] != "admin"): raise HTTPException(404, "Return not found")
    return dict(r)
@app.patch("/returns/{rid}/status")
def status(rid: int, b: S, u: dict = Depends(U)):
    if u["role"] != "admin": raise HTTPException(403, "Admin only")
    r = db().execute("select * from returns where id=?", (rid,)).fetchone()
    if not r: raise HTTPException(404, "Return not found")
    if b.status not in FLOW.get(r["status"], []): raise HTTPException(400, f"Cannot move from {r['status']} to {b.status}")
    if b.status == "Received":  # items physically accepted -> restore stock
        try:
            o = httpx.get(f"{ORDERS}/orders/{r['order_id']}", headers=H(u), timeout=5).json()
            for i in (json.loads(r["items"]) if r["items"] else o["items"]):
                httpx.patch(f"{INVENTORY}/inventory/{i['product_id']}/stock", json={"delta": i["qty"], "reason": f"return #{rid}"}, headers=H(u), timeout=5).raise_for_status()
        except httpx.HTTPError: raise HTTPException(503, "Could not restore stock, try again")
    with db() as c: c.execute("update returns set status=?, history=history||' > '||? where id=?", (b.status, b.status, rid))
    if b.status == "Refunded":
        try: httpx.post(f"{PAYMENTS}/payments/refund-order", json={"order_id": r["order_id"]}, headers={"X-Internal-Key": os.getenv("INTERNAL_KEY", "internal")}, timeout=5)
        except httpx.HTTPError: pass
    return {"id": rid, "status": b.status}

try:
    with db() as c: c.execute("alter table returns add column items text")
except sqlite3.OperationalError: pass
