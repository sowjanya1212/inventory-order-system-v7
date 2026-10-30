import os, sqlite3, hashlib, secrets, time, jwt
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, Field
SECRET = os.getenv("JWT_SECRET", "dev-secret")
app = FastAPI(title="Auth Service")
def db():
    c = sqlite3.connect("auth.db"); c.row_factory = sqlite3.Row; return c
def hp(p, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 100000).hex()
def tok(uid, role, kind, secs):
    return jwt.encode({"sub": str(uid), "jti": secrets.token_hex(6), "role": role, "type": kind, "exp": int(time.time()) + secs}, SECRET, "HS256")
with db() as c:
    c.execute("create table if not exists users(id integer primary key, email text unique, pw text, role text)")
    if not c.execute("select 1 from users where email='admin@example.com'").fetchone():
        c.execute("insert into users(email,pw,role) values('admin@example.com',?,'admin')", (hp("Admin@123"),))
class Cred(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=6)
class Ref(BaseModel):
    refresh_token: str
def pair(u):
    rt = tok(u["id"], u["role"], "refresh", 86400 * 7)
    with db() as c: c.execute("insert into refresh_tokens(token_hash,user_id,revoked) values(?,?,0)", (hashlib.sha256(rt.encode()).hexdigest(), u["id"]))
    return {"access_token": tok(u["id"], u["role"], "access", 900), "refresh_token": rt, "role": u["role"]}
@app.get("/health")
def health(): return {"status": "ok"}
@app.post("/auth/register", status_code=201)
def register(b: Cred):
    try:
        with db() as c: c.execute("insert into users(email,pw,role) values(?,?,'customer')", (b.email.lower(), hp(b.password)))
    except sqlite3.IntegrityError: raise HTTPException(409, "Email already registered")
    audit(b.email, "register")
    return {"message": "registered"}
@app.post("/auth/login")
def login(b: Cred):
    u = db().execute("select * from users where email=?", (b.email.lower(),)).fetchone()
    if not u: raise HTTPException(401, "Invalid email or password")
    salt = u["pw"].split("$")[0]
    if not secrets.compare_digest(hp(b.password, salt), u["pw"]): raise HTTPException(401, "Invalid email or password")
    audit(u["id"], "login")
    return pair(u)
@app.post("/auth/refresh")
def refresh(b: Ref):
    try: d = jwt.decode(b.refresh_token, SECRET, ["HS256"])
    except jwt.PyJWTError: raise HTTPException(401, "Invalid refresh token")
    if d["type"] != "refresh": raise HTTPException(401, "Wrong token type")
    with db() as c: n = c.execute("update refresh_tokens set revoked=1 where token_hash=? and revoked=0", (hashlib.sha256(b.refresh_token.encode()).hexdigest(),)).rowcount
    if not n: raise HTTPException(401, "Refresh token already used or revoked")
    u = db().execute("select * from users where id=?", (d["sub"],)).fetchone()
    return pair(u)
@app.get("/auth/me")
def me(x_user_id: str = Header(None)):
    u = db().execute("select id,email,role from users where id=?", (x_user_id,)).fetchone()
    if not u: raise HTTPException(401, "Login required")
    return dict(u)

with db() as c: c.execute("create table if not exists audit(id integer primary key, ts text default current_timestamp, actor text, action text)")
def audit(actor, action):  # never log passwords or tokens
    with db() as c: c.execute("insert into audit(actor,action) values(?,?)", (str(actor), action))
def adm(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    if not x_user_id: raise HTTPException(401, "Login required")
    if x_user_role != "admin": raise HTTPException(403, "Admin only")
    return x_user_id
class Role(BaseModel):
    role: str = Field(pattern="^(customer|admin|inventory_manager)$")
@app.get("/auth/users")
def users(a: str = Depends(adm)): return [dict(r) for r in db().execute("select id,email,role from users")]
@app.patch("/auth/users/{uid}/role")
def set_role(uid: int, b: Role, a: str = Depends(adm)):
    with db() as c: n = c.execute("update users set role=? where id=?", (b.role, uid)).rowcount
    if not n: raise HTTPException(404, "User not found")
    audit(a, f"role of user {uid} set to {b.role}"); return {"id": uid, "role": b.role}
@app.get("/auth/audit")
def audit_log(a: str = Depends(adm)): return [dict(r) for r in db().execute("select * from audit order by id desc limit 200")]

with db() as c: c.execute("create table if not exists refresh_tokens(token_hash text primary key, user_id int, revoked int default 0, created text default current_timestamp)")
@app.post("/auth/logout")
def logout(b: Ref):
    with db() as c: c.execute("update refresh_tokens set revoked=1 where token_hash=?", (hashlib.sha256(b.refresh_token.encode()).hexdigest(),))
    return {"message": "logged out"}
