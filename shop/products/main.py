import os, sqlite3, json, httpx, jwt
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
SECRET = os.getenv("JWT_SECRET", "dev-secret")
INVENTORY = os.getenv("INVENTORY_URL", "http://127.0.0.1:8003"); REVIEWS = os.getenv("REVIEWS_URL", "http://127.0.0.1:8010")
app = FastAPI(title="Product Catalog Service")

from fastapi import Depends
def U(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    """Identity is set by the gateway after it checks the login token once."""
    if not x_user_id: raise HTTPException(401, "Login required")
    return {"sub": x_user_id, "role": x_user_role}
def H(u): return {"X-User-Id": u["sub"], "X-User-Role": u["role"]}
def db():
    c = sqlite3.connect("products.db"); c.row_factory = sqlite3.Row; return c
def admin(u):
    if u["role"] not in ("admin", "inventory_manager"): raise HTTPException(403, "Admin only")
class P(BaseModel):
    name: str = Field(min_length=1); description: str = ""; category: str = Field(min_length=1)
    brand: str = ""; price: float = Field(gt=0); image: str = ""
with db() as c:
    c.execute("create table if not exists products(id integer primary key, name text, description text, category text, brand text, price real, image text)")
    if not c.execute("select 1 from products").fetchone():
        c.executemany("insert into products(name,description,category,brand,price,image) values(?,?,?,?,?,?)", [
            ("Wireless Mouse", "2.4GHz ergonomic mouse", "Electronics", "Logi", 799, ""),
            ("Notebook A5", "200 pages ruled", "Stationery", "Classmate", 120, ""),
            ("Water Bottle", "1L steel bottle", "Home", "Milton", 450, "")])
@app.get("/health")
def health(): return {"status": "ok"}
@app.get("/products")
def lst(q: str = "", category: str = "", min_price: float = 0, max_price: float = 1e12, sort: str = "id", page: int = 1, size: int = 10, min_rating: float = 0, in_stock: bool = False):
    sort = sort if sort in ("id", "price", "name") else "id"
    w = "name like ? and category like ? and price between ? and ?"
    a = (f"%{q}%", f"%{category}%", min_price, max_price)
    c = db()
    if min_rating or in_stock:  # filters that need other services
        items = [dict(r) for r in c.execute(f"select * from products where {w} order by {sort}", a)]
        if in_stock:
            try: have = {s["product_id"]: s["qty"] for s in httpx.get(f"{INVENTORY}/inventory", timeout=5).json()}
            except httpx.HTTPError: raise HTTPException(503, "Inventory service unavailable")
            items = [p for p in items if have.get(p["id"], 0) > 0]
        if min_rating:
            keep = []
            for p in items:
                try: avg = httpx.get(f"{REVIEWS}/products/{p['id']}/reviews", timeout=5).json()["average"]
                except Exception: avg = 0
                if avg >= min_rating: keep.append(p)
            items = keep
        return {"total": len(items), "page": page, "items": items[(page - 1) * size: page * size]}
    total = c.execute(f"select count(*) from products where {w}", a).fetchone()[0]
    rows = c.execute(f"select * from products where {w} order by {sort} limit ? offset ?", a + (size, (page - 1) * size)).fetchall()
    return {"total": total, "page": page, "items": [dict(r) for r in rows]}
@app.get("/products/compare")
def compare(ids: str):
    out = []
    for i in [x for x in ids.split(",") if x.strip().isdigit()][:4]:
        r = db().execute("select * from products where id=?", (int(i),)).fetchone()
        if r: d = dict(r); d["specs"] = json.loads(d.get("specs") or "{}"); out.append(d)
    return out
@app.get("/products/recently-viewed")
def recent(x_user_id: str = Header(None)):
    if not x_user_id: raise HTTPException(401, "Login required")
    c = db(); ids = [r[0] for r in c.execute("select product_id from views where user_id=? group by product_id order by max(id) desc limit 10", (x_user_id,))]
    return [dict(c.execute("select * from products where id=?", (i,)).fetchone()) for i in ids if c.execute("select 1 from products where id=?", (i,)).fetchone()]
@app.put("/products/{pid}/specs")
def set_specs(pid: int, b: dict, u: dict = Depends(U)):
    admin(u)
    with db() as c: n = c.execute("update products set specs=? where id=?", (json.dumps(b), pid)).rowcount
    if not n: raise HTTPException(404, "Product not found")
    return {"id": pid, "specs": b}
@app.get("/products/{pid}")
def get(pid: int, x_user_id: str = Header(None)):
    r = db().execute("select * from products where id=?", (pid,)).fetchone()
    if not r: raise HTTPException(404, "Product not found")
    if x_user_id:
        with db() as c: c.execute("insert into views(user_id,product_id) values(?,?)", (x_user_id, pid))
    d = dict(r); d["specs"] = json.loads(d.get("specs") or "{}"); return d
@app.post("/products", status_code=201)
def create(p: P, u: dict = Depends(U)):
    admin(u)
    with db() as c:
        cur = c.execute("insert into products(name,description,category,brand,price,image) values(?,?,?,?,?,?)", (p.name, p.description, p.category, p.brand, p.price, p.image))
    return {"id": cur.lastrowid, **p.model_dump()}
@app.put("/products/{pid}")
def update(pid: int, p: P, u: dict = Depends(U)):
    admin(u)
    with db() as c:
        n = c.execute("update products set name=?,description=?,category=?,brand=?,price=?,image=? where id=?", (p.name, p.description, p.category, p.brand, p.price, p.image, pid)).rowcount
    if not n: raise HTTPException(404, "Product not found")
    return {"id": pid, **p.model_dump()}
@app.delete("/products/{pid}", status_code=204)
def delete(pid: int, u: dict = Depends(U)):
    admin(u)
    with db() as c: c.execute("delete from products where id=?", (pid,))

import csv, io
from fastapi import Request
@app.post("/products/bulk-import", status_code=201, openapi_extra={"requestBody": {"required": True, "content": {"text/csv": {"schema": {"type": "string", "example": "name,description,category,brand,price,image\\nPen,Blue pen,Stationery,Cello,10,"}}}}})
async def bulk(req: Request, u: dict = Depends(U)):
    """Send CSV text (header: name,description,category,brand,price,image). Invalid rows are skipped and reported."""
    admin(u); text = (await req.body()).decode("utf-8-sig"); ok, errors = 0, []
    for n, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        try:
            p = P(name=(row.get("name") or "").strip(), description=row.get("description") or "", category=(row.get("category") or "").strip(), brand=row.get("brand") or "", price=float(row.get("price") or 0), image=row.get("image") or "")
            with db() as c: c.execute("insert into products(name,description,category,brand,price,image) values(?,?,?,?,?,?)", (p.name, p.description, p.category, p.brand, p.price, p.image))
            ok += 1
        except Exception as e: errors.append({"row": n, "error": str(e)[:80]})
    return {"imported": ok, "errors": errors}

with db() as c: c.execute("create table if not exists views(id integer primary key, user_id text, product_id int)")
try:
    with db() as c: c.execute("alter table products add column specs text")
except sqlite3.OperationalError: pass
