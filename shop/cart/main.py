import os, sqlite3, httpx
from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel, Field
PRODUCTS = os.getenv("PRODUCTS_URL", "http://127.0.0.1:8002"); INVENTORY = os.getenv("INVENTORY_URL", "http://127.0.0.1:8003")
app = FastAPI(title="Cart & Wishlist Service")
def U(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    if not x_user_id: raise HTTPException(401, "Login required")
    return {"sub": x_user_id, "role": x_user_role}
def db():
    c = sqlite3.connect("cart.db"); c.row_factory = sqlite3.Row; return c
with db() as c:
    c.execute("create table if not exists cart_items(id integer primary key, user_id text, product_id int, qty int check(qty>0), unique(user_id,product_id))")
    c.execute("create table if not exists wishlist(id integer primary key, user_id text, product_id int, unique(user_id,product_id))")
class Add(BaseModel):
    product_id: int; qty: int = Field(1, ge=1, le=100)
class Qty(BaseModel):
    qty: int = Field(ge=1, le=100)
class W(BaseModel):
    product_id: int
def product(pid):
    try: r = httpx.get(f"{PRODUCTS}/products/{pid}", timeout=5)
    except httpx.HTTPError: raise HTTPException(503, "Product service unavailable")
    if r.status_code != 200: raise HTTPException(404, "Product not found")
    return r.json()
def stock(pid):
    try: return httpx.get(f"{INVENTORY}/inventory/{pid}", timeout=5).json()["qty"]
    except httpx.HTTPError: raise HTTPException(503, "Inventory service unavailable")
@app.get("/health")
def health(): return {"status": "ok"}
@app.get("/cart")
def cart(u: dict = Depends(U)):
    items, total = [], 0
    for r in db().execute("select * from cart_items where user_id=? order by id", (u["sub"],)):
        p = product(r["product_id"]); ok = stock(r["product_id"]) >= r["qty"]
        items.append({"id": r["id"], "product_id": r["product_id"], "name": p["name"], "price": p["price"], "qty": r["qty"], "available": ok}); total += p["price"] * r["qty"]
    return {"items": items, "total": round(total, 2)}
@app.post("/cart/items", status_code=201)
def add(b: Add, u: dict = Depends(U)):
    product(b.product_id); c = db()
    r = c.execute("select qty from cart_items where user_id=? and product_id=?", (u["sub"], b.product_id)).fetchone()
    new = (r["qty"] if r else 0) + b.qty; have = stock(b.product_id)
    if have < new: raise HTTPException(409, f"Only {have} in stock")
    with c: c.execute("insert into cart_items(user_id,product_id,qty) values(?,?,?) on conflict(user_id,product_id) do update set qty=?", (u["sub"], b.product_id, new, new))
    return {"product_id": b.product_id, "qty": new}
@app.patch("/cart/items/{iid}")
def upd(iid: int, b: Qty, u: dict = Depends(U)):
    r = db().execute("select * from cart_items where id=? and user_id=?", (iid, u["sub"])).fetchone()
    if not r: raise HTTPException(404, "Cart item not found")
    if stock(r["product_id"]) < b.qty: raise HTTPException(409, "Not enough stock")
    with db() as c: c.execute("update cart_items set qty=? where id=?", (b.qty, iid))
    return {"id": iid, "qty": b.qty}
@app.delete("/cart/items/{iid}", status_code=204)
def rem(iid: int, u: dict = Depends(U)):
    with db() as c: c.execute("delete from cart_items where id=? and user_id=?", (iid, u["sub"]))
@app.get("/wishlist")
def wl(u: dict = Depends(U)):
    return [{"id": r["id"], "product_id": r["product_id"], **{k: product(r["product_id"])[k] for k in ("name", "price")}} for r in db().execute("select * from wishlist where user_id=?", (u["sub"],))]
@app.post("/wishlist/items", status_code=201)
def wadd(b: W, u: dict = Depends(U)):
    product(b.product_id)
    with db() as c: c.execute("insert or ignore into wishlist(user_id,product_id) values(?,?)", (u["sub"], b.product_id))
    return {"product_id": b.product_id}
@app.delete("/wishlist/items/{iid}", status_code=204)
def wrem(iid: int, u: dict = Depends(U)):
    with db() as c: c.execute("delete from wishlist where id=? and user_id=?", (iid, u["sub"]))
