import os, sqlite3, httpx
from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel, Field
ORDERS = os.getenv("ORDERS_URL", "http://127.0.0.1:8004")
app = FastAPI(title="Review & Rating Service")
def U(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    if not x_user_id: raise HTTPException(401, "Login required")
    return {"sub": x_user_id, "role": x_user_role}
def H(u): return {"X-User-Id": u["sub"], "X-User-Role": u["role"]}
def db():
    c = sqlite3.connect("reviews.db"); c.row_factory = sqlite3.Row; return c
with db() as c:
    c.execute("create table if not exists reviews(id integer primary key, product_id int, user_id text, rating int check(rating between 1 and 5), comment text, hidden int default 0, created text default current_timestamp, unique(product_id,user_id))")
class R(BaseModel):
    product_id: int; rating: int = Field(ge=1, le=5); comment: str = ""
class E(BaseModel):
    rating: int = Field(ge=1, le=5); comment: str = ""
class Hide(BaseModel):
    hidden: bool
@app.get("/health")
def health(): return {"status": "ok"}
@app.post("/reviews", status_code=201)
def create(b: R, u: dict = Depends(U)):
    try: os_ = httpx.get(f"{ORDERS}/orders/my-orders", headers=H(u), timeout=5).json()
    except httpx.HTTPError: raise HTTPException(503, "Order service unavailable")
    if not any(o["status"] == "Delivered" and any(i["product_id"] == b.product_id for i in o["items"]) for o in os_):
        raise HTTPException(403, "Only customers who received this product can review it")
    try:
        with db() as c: cur = c.execute("insert into reviews(product_id,user_id,rating,comment) values(?,?,?,?)", (b.product_id, u["sub"], b.rating, b.comment))
    except sqlite3.IntegrityError: raise HTTPException(409, "You already reviewed this product (edit your review instead)")
    return {"id": cur.lastrowid}
@app.get("/products/{pid}/reviews")
def lst(pid: int):
    rows = [dict(r) for r in db().execute("select id,rating,comment,created from reviews where product_id=? and hidden=0 order by id desc", (pid,))]
    return {"average": round(sum(r["rating"] for r in rows) / len(rows), 1) if rows else 0, "count": len(rows), "reviews": rows}
def own(rid, u, admin_ok=False):
    r = db().execute("select * from reviews where id=?", (rid,)).fetchone()
    if not r or (r["user_id"] != u["sub"] and not (admin_ok and u["role"] == "admin")): raise HTTPException(404, "Review not found")
@app.put("/reviews/{rid}")
def edit(rid: int, b: E, u: dict = Depends(U)):
    own(rid, u)
    with db() as c: c.execute("update reviews set rating=?, comment=? where id=?", (b.rating, b.comment, rid))
    return {"id": rid}
@app.delete("/reviews/{rid}", status_code=204)
def delete(rid: int, u: dict = Depends(U)):
    own(rid, u, admin_ok=True)
    with db() as c: c.execute("delete from reviews where id=?", (rid,))
@app.patch("/reviews/{rid}/hide")
def hide(rid: int, b: Hide, u: dict = Depends(U)):
    if u["role"] != "admin": raise HTTPException(403, "Admin only")
    with db() as c: n = c.execute("update reviews set hidden=? where id=?", (int(b.hidden), rid)).rowcount
    if not n: raise HTTPException(404, "Review not found")
    return {"id": rid, "hidden": b.hidden}
