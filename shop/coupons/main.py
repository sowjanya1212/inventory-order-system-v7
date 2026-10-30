import os, sqlite3, jwt, datetime
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
SECRET = os.getenv("JWT_SECRET", "dev-secret"); KEY = os.getenv("INTERNAL_KEY", "internal")
app = FastAPI(title="Coupon Service")

from fastapi import Depends
def U(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    """Identity is set by the gateway after it checks the login token once."""
    if not x_user_id: raise HTTPException(401, "Login required")
    return {"sub": x_user_id, "role": x_user_role}
def H(u): return {"X-User-Id": u["sub"], "X-User-Role": u["role"]}
def db():
    c = sqlite3.connect("coupons.db", isolation_level=None); c.row_factory = sqlite3.Row; return c
c0 = db()
c0.execute("create table if not exists coupons(code text primary key, kind text, value real, min_purchase real, expires text, max_uses int, used int default 0)")
c0.execute("insert or ignore into coupons values('WELCOME10','percent',10,100,'2030-12-31',1000,0)")
def role(u): return u["role"]
class C(BaseModel):
    code: str = Field(min_length=3); kind: str = Field(pattern="^(percent|fixed)$"); value: float = Field(gt=0)
    min_purchase: float = Field(0, ge=0); expires: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$"); max_uses: int = Field(gt=0)
class V(BaseModel):
    code: str; subtotal: float
def calc(c, code, subtotal):
    r = c.execute("select * from coupons where code=?", (code.upper(),)).fetchone()
    if not r: raise HTTPException(400, "Invalid coupon")
    if r["expires"] < datetime.date.today().isoformat(): raise HTTPException(400, "Coupon expired")
    if r["used"] >= r["max_uses"]: raise HTTPException(400, "Coupon usage limit reached")
    if subtotal < r["min_purchase"]: raise HTTPException(400, f"Minimum purchase is {r['min_purchase']}")
    d = subtotal * r["value"] / 100 if r["kind"] == "percent" else r["value"]
    return round(min(d, subtotal), 2)
@app.get("/health")
def health(): return {"status": "ok"}
@app.post("/coupons", status_code=201)
def create(b: C, u: dict = Depends(U)):
    if role(u) != "admin": raise HTTPException(403, "Admin only")
    try: db().execute("insert into coupons values(?,?,?,?,?,?,0)", (b.code.upper(), b.kind, b.value, b.min_purchase, b.expires, b.max_uses))
    except sqlite3.IntegrityError: raise HTTPException(409, "Code already exists")
    return {"code": b.code.upper()}
@app.get("/coupons")
def lst(u: dict = Depends(U)):
    if role(u) != "admin": raise HTTPException(403, "Admin only")
    return [dict(r) for r in db().execute("select * from coupons")]
@app.post("/coupons/validate")
def validate(b: V, u: dict = Depends(U)):
    role(u); return {"discount": calc(db(), b.code, b.subtotal)}
@app.post("/coupons/redeem")
def redeem(b: V, x_internal_key: str = Header(None)):
    if x_internal_key != KEY: raise HTTPException(403, "Internal only")
    c = db(); c.execute("begin immediate")
    try: d = calc(c, b.code, b.subtotal)
    except HTTPException: c.execute("rollback"); raise
    c.execute("update coupons set used=used+1 where code=?", (b.code.upper(),)); c.execute("commit")
    return {"discount": d}
