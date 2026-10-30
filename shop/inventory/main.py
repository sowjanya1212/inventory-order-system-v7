import os, sqlite3, json, jwt
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from events import publish
SECRET = os.getenv("JWT_SECRET", "dev-secret"); KEY = os.getenv("INTERNAL_KEY", "internal")
app = FastAPI(title="Inventory Service")

from fastapi import Depends
def U(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    """Identity is set by the gateway after it checks the login token once."""
    if not x_user_id: raise HTTPException(401, "Login required")
    return {"sub": x_user_id, "role": x_user_role}
def H(u): return {"X-User-Id": u["sub"], "X-User-Role": u["role"]}
def db():
    c = sqlite3.connect("inventory.db", isolation_level=None); c.row_factory = sqlite3.Row; return c
c0 = db()
c0.execute("create table if not exists stock(product_id integer primary key, qty integer check(qty>=0))")
c0.execute("create table if not exists reservations(id integer primary key, items text, status text)")
c0.execute("create table if not exists txns(id integer primary key, product_id int, delta int, reason text, ts text default current_timestamp)")
for i in (1, 2, 3): c0.execute("insert or ignore into stock values(?,50)", (i,))
def role(u): return u["role"]
class Adj(BaseModel):
    delta: int; reason: str = Field(min_length=1)
class Item(BaseModel):
    product_id: int; qty: int = Field(gt=0)
class Res(BaseModel):
    items: list[Item]
@app.get("/health")
def health(): return {"status": "ok"}
@app.get("/inventory")
def all_(): return [dict(r) for r in db().execute("select * from stock")]
@app.get("/inventory/{pid}")
def one(pid: int):
    r = db().execute("select * from stock where product_id=?", (pid,)).fetchone()
    return dict(r) if r else {"product_id": pid, "qty": 0}
@app.patch("/inventory/{pid}/stock")
def adjust(pid: int, b: Adj, u: dict = Depends(U)):
    if role(u) not in ("admin", "inventory_manager"): raise HTTPException(403, "Admin only")
    c = db(); c.execute("begin immediate")
    c.execute("insert or ignore into stock values(?,0)", (pid,))
    q = c.execute("select qty from stock where product_id=?", (pid,)).fetchone()[0]
    if q + b.delta < 0: c.execute("rollback"); raise HTTPException(400, "Stock cannot go negative")
    c.execute("update stock set qty=qty+? where product_id=?", (b.delta, pid))
    c.execute("insert into txns(product_id,delta,reason,actor) values(?,?,?,?)", (pid, b.delta, b.reason, u["sub"])); c.execute("commit")
    return {"product_id": pid, "qty": q + b.delta}
@app.get("/inventory/{pid}/transactions")
def txns(pid: int): return [dict(r) for r in db().execute("select * from txns where product_id=? order by id desc", (pid,))]
@app.post("/inventory/reservations")
def reserve(b: Res, x_internal_key: str = Header(None)):
    if x_internal_key != KEY: raise HTTPException(403, "Internal only")
    c = db(); c.execute("begin immediate")
    for i in b.items:
        r = c.execute("select qty from stock where product_id=?", (i.product_id,)).fetchone()
        if not r or r[0] < i.qty: c.execute("rollback"); raise HTTPException(409, f"Insufficient stock for product {i.product_id}")
    for i in b.items:
        c.execute("update stock set qty=qty-? where product_id=?", (i.qty, i.product_id))
        c.execute("insert into txns(product_id,delta,reason) values(?,?,'reservation')", (i.product_id, -i.qty))
    cur = c.execute("insert into reservations(items,status) values(?, 'active')", (json.dumps([i.model_dump() for i in b.items]),))
    c.execute("commit")
    for i in b.items:
        q = c.execute("select qty from stock where product_id=?", (i.product_id,)).fetchone()[0]
        t = c.execute("select threshold from thresholds where product_id=?", (i.product_id,)).fetchone()
        if q <= (t[0] if t else 10): publish("stock.low", user_id="admin", product_id=i.product_id, qty=q)  # admin low-stock alert
    return {"reservation_id": cur.lastrowid}
@app.post("/inventory/reservations/{rid}/release")
def release(rid: int, x_internal_key: str = Header(None)):
    if x_internal_key != KEY: raise HTTPException(403, "Internal only")
    c = db(); c.execute("begin immediate")
    r = c.execute("select * from reservations where id=?", (rid,)).fetchone()
    if not r: c.execute("rollback"); raise HTTPException(404, "Reservation not found")
    if r["status"] == "active":
        for i in json.loads(r["items"]):
            c.execute("update stock set qty=qty+? where product_id=?", (i["qty"], i["product_id"]))
            c.execute("insert into txns(product_id,delta,reason) values(?,?,'release')", (i["product_id"], i["qty"]))
        c.execute("update reservations set status='released' where id=?", (rid,))
    c.execute("commit"); return {"status": "released"}

c0.execute("create table if not exists thresholds(product_id integer primary key, threshold int)")
try: c0.execute("alter table txns add column actor text")
except sqlite3.OperationalError: pass
class Thr(BaseModel):
    threshold: int = Field(ge=0)
class Restock(BaseModel):
    items: list[Item]; reason: str = ""
@app.patch("/inventory/{pid}/threshold")
def set_threshold(pid: int, b: Thr, u: dict = Depends(U)):
    if role(u) not in ("admin", "inventory_manager"): raise HTTPException(403, "Admin only")
    db().execute("insert into thresholds values(?,?) on conflict(product_id) do update set threshold=?", (pid, b.threshold, b.threshold))
    return {"product_id": pid, "threshold": b.threshold}
@app.get("/inventory/report/movements")
def report(u: dict = Depends(U)):
    """Stock movement / audit report: totals per product and the latest entries with who made them."""
    if role(u) not in ("admin", "inventory_manager"): raise HTTPException(403, "Admin only")
    c = db()
    return {"per_product": [dict(r) for r in c.execute("select product_id, sum(case when delta>0 then delta else 0 end) added, sum(case when delta<0 then -delta else 0 end) removed, count(*) movements from txns group by product_id")],
            "latest": [dict(r) for r in c.execute("select * from txns order by id desc limit 50")]}
@app.post("/inventory/restock")
def restock(b: Restock, x_internal_key: str = Header(None)):
    if x_internal_key != KEY: raise HTTPException(403, "Internal only")
    c = db(); c.execute("begin immediate")
    for i in b.items:
        c.execute("insert or ignore into stock values(?,0)", (i.product_id,)); c.execute("update stock set qty=qty+? where product_id=?", (i.qty, i.product_id))
        c.execute("insert into txns(product_id,delta,reason,actor) values(?,?,?,'system')", (i.product_id, i.qty, b.reason or "restock"))
    c.execute("commit"); return {"status": "restocked"}
