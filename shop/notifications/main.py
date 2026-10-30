import os, sqlite3, json, time, threading
from fastapi import FastAPI, Depends, Header, HTTPException
RABBIT = os.getenv("RABBITMQ_URL", ""); KEY = os.getenv("INTERNAL_KEY", "internal")
app = FastAPI(title="Notification Service")
def U(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    if not x_user_id: raise HTTPException(401, "Login required")
    return {"sub": x_user_id, "role": x_user_role}
def db():
    c = sqlite3.connect("notifications.db"); c.row_factory = sqlite3.Row; return c
with db() as c:
    c.execute("create table if not exists notifications(id integer primary key, event_id text unique, user_id text, title text, body text, is_read int default 0, created text default current_timestamp)")
    c.execute("create table if not exists failed_events(id integer primary key, payload text, error text)")
MSG = {"order.created": ("Order placed", "Order {order_number} was placed. Please complete the payment."),
       "order.paid": ("Order confirmed", "Payment received. Order {order_number} is confirmed."),
       "order.shipped": ("Order shipped", "Order {order_number} has been shipped."),
       "order.delivered": ("Order delivered", "Order {order_number} was delivered. You can now review the products."),
       "order.cancelled": ("Order cancelled", "Order {order_number} was cancelled."),
       "payment.failed": ("Payment failed", "Payment for order {order_number} failed. You can try again."),
       "stock.low": ("Low stock alert", "Product {product_id} is low on stock ({qty} left).")}
def handle(ev):
    for attempt in range(3):  # retry handling
        try:
            title, body = MSG[ev["kind"]]
            with db() as c:  # unique event_id = duplicate events are ignored
                c.execute("insert or ignore into notifications(event_id,user_id,title,body) values(?,?,?,?)", (ev["event_id"], ev["user_id"], title, body.format(**ev)))
            return
        except Exception as e:
            err = str(e); time.sleep(0.2)
    with db() as c: c.execute("insert into failed_events(payload,error) values(?,?)", (json.dumps(ev), err))  # dead-letter table
    raise RuntimeError(err)
def consume():
    import pika
    while True:
        try:
            ch = pika.BlockingConnection(pika.URLParameters(RABBIT)).channel(); ch.queue_declare("notifications", durable=True)
            def cb(c_, m, p, body):
                try: handle(json.loads(body)); c_.basic_ack(m.delivery_tag)
                except Exception: c_.basic_nack(m.delivery_tag, requeue=False)
            ch.basic_consume("notifications", cb); ch.start_consuming()
        except Exception: time.sleep(3)
if RABBIT: threading.Thread(target=consume, daemon=True).start()
@app.get("/health")
def health(): return {"status": "ok"}
@app.post("/events", status_code=202)
def events(ev: dict, x_internal_key: str = Header(None)):
    if x_internal_key != KEY: raise HTTPException(403, "Internal only")
    handle(ev); return {"accepted": True}
@app.get("/notifications")
def lst(u: dict = Depends(U)):
    ids = (u["sub"], "admin") if u["role"] == "admin" else (u["sub"], u["sub"])
    return [dict(r) for r in db().execute("select * from notifications where user_id in (?,?) order by id desc", ids)]
@app.patch("/notifications/{nid}/read")
def read(nid: int, u: dict = Depends(U)):
    ids = (u["sub"], "admin") if u["role"] == "admin" else (u["sub"], u["sub"])
    with db() as c: n = c.execute("update notifications set is_read=1 where id=? and user_id in (?,?)", (nid, *ids)).rowcount
    if not n: raise HTTPException(404, "Notification not found")
    return {"id": nid, "is_read": True}
