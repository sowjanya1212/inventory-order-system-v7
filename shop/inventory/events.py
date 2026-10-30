"""Outbox pattern: every event is first saved in a local table, then sent. Unsent events are retried on the next publish."""
import os, json, uuid, sqlite3, httpx
RABBIT = os.getenv("RABBITMQ_URL", ""); NOTIFY = os.getenv("NOTIFY_URL", "http://127.0.0.1:8009"); KEY = os.getenv("INTERNAL_KEY", "internal")
def _db():
    c = sqlite3.connect("outbox.db"); c.execute("create table if not exists outbox(id integer primary key, payload text, sent int default 0)"); return c
def _send(ev):
    if RABBIT:
        import pika
        c = pika.BlockingConnection(pika.URLParameters(RABBIT)); ch = c.channel(); ch.queue_declare("notifications", durable=True)
        ch.basic_publish("", "notifications", json.dumps(ev), pika.BasicProperties(delivery_mode=2)); c.close()
    else: httpx.post(f"{NOTIFY}/events", json=ev, headers={"X-Internal-Key": KEY}, timeout=3).raise_for_status()
def flush():
    c = _db()
    for i, p in c.execute("select id,payload from outbox where sent=0 order by id").fetchall():
        try: _send(json.loads(p))
        except Exception: continue  # stays unsent, retried later; receiver ignores duplicates by event_id
        with c: c.execute("update outbox set sent=1 where id=?", (i,))
    c.close()
def publish(kind, **data):
    ev = {"event_id": uuid.uuid4().hex, "kind": kind, **data}
    c = _db()
    with c: c.execute("insert into outbox(payload) values(?)", (json.dumps(ev),))
    c.close(); flush()
