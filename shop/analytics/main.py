import os, jwt, httpx, csv, io, datetime
from collections import Counter, defaultdict
from fastapi import FastAPI, HTTPException, Header, Response
SECRET = os.getenv("JWT_SECRET", "dev-secret")
ORDERS = os.getenv("ORDERS_URL", "http://127.0.0.1:8004"); INVENTORY = os.getenv("INVENTORY_URL", "http://127.0.0.1:8003"); RETURNS = os.getenv("RETURNS_URL", "http://127.0.0.1:8006")
app = FastAPI(title="Analytics Service")

from fastapi import Depends
def U(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    """Identity is set by the gateway after it checks the login token once."""
    if not x_user_id: raise HTTPException(401, "Login required")
    return {"sub": x_user_id, "role": x_user_role}
def H(u): return {"X-User-Id": u["sub"], "X-User-Role": u["role"]}
def get(url, u):
    if u["role"] != "admin": raise HTTPException(403, "Admin only")
    try: r = httpx.get(url, headers=H(u), timeout=5); r.raise_for_status(); return r.json()
    except httpx.HTTPError: raise HTTPException(503, "Upstream service unavailable")
def live(a, frm, to):
    return [o for o in get(f"{ORDERS}/orders", a) if (not frm or o["created"][:10] >= frm) and (not to or o["created"][:10] <= to)]
@app.get("/health")
def health(): return {"status": "ok"}
@app.get("/analytics/orders")
def orders(u: dict = Depends(U), frm: str = "", to: str = ""):
    os_ = live(u, frm, to); n = len(os_); ret = len(get(f"{RETURNS}/returns", u))
    ok = [o for o in os_ if o["status"] != "Cancelled"]
    return {"total_orders": n, "revenue": round(sum(o["total"] for o in ok), 2), "by_status": dict(Counter(o["status"] for o in os_)),
            "cancellations": n - len(ok), "return_rate_pct": round(100 * ret / n, 1) if n else 0}
@app.get("/analytics/sales")
def sales(u: dict = Depends(U), frm: str = "", to: str = "", period: str = "daily", format: str = "json"):
    days = defaultdict(float)
    for o in live(u, frm, to):
        if o["status"] != "Cancelled":
            d = o["created"][:10]; k = {"monthly": d[:7], "weekly": "%d-W%02d" % tuple(datetime.date.fromisoformat(d).isocalendar()[:2])}.get(period, d)
            days[k] += o["total"]
    rows = [{"period": k, "revenue": round(v, 2)} for k, v in sorted(days.items())]
    if format == "csv":
        s = io.StringIO(); w = csv.DictWriter(s, ["period", "revenue"]); w.writeheader(); w.writerows(rows)
        return Response(s.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=sales.csv"})
    return rows
@app.get("/analytics/top-products")
def top(u: dict = Depends(U)):
    c = Counter()
    for o in live(u, "", ""):
        if o["status"] != "Cancelled":
            for i in o["items"]: c[i["name"]] += i["qty"]
    return [{"name": k, "units": v} for k, v in c.most_common(5)]
@app.get("/analytics/inventory")
def inv(u: dict = Depends(U), threshold: int = 10):
    s = get(f"{INVENTORY}/inventory", u)
    return {"out_of_stock": [x for x in s if x["qty"] == 0], "low_stock": [x for x in s if 0 < x["qty"] <= threshold]}

@app.get("/analytics/top-categories")
def top_categories(u: dict = Depends(U)):
    c = Counter()
    for o in live(u, "", ""):
        if o["status"] != "Cancelled":
            for i in o["items"]: c[i.get("category", "Unknown")] += i["price"] * i["qty"]
    return [{"category": k, "revenue": round(v, 2)} for k, v in c.most_common(5)]
@app.get("/analytics/report.pdf")
def report_pdf(u: dict = Depends(U)):
    from reportlab.pdfgen import canvas
    s = orders(u); buf = io.BytesIO(); p = canvas.Canvas(buf); y = 800
    p.setFont("Helvetica-Bold", 16); p.drawString(60, y, "Sales Report"); p.setFont("Helvetica", 11); y -= 30
    for line in [f"Total orders: {s['total_orders']}", f"Revenue: {s['revenue']}", f"Cancellations: {s['cancellations']}", f"Return rate: {s['return_rate_pct']}%", "Order status: " + str(s["by_status"]), "", "Top products:"] + [f"  {t['name']} - {t['units']} units" for t in top(u)]:
        p.drawString(60, y, line); y -= 18
    p.save(); return Response(buf.getvalue(), media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=sales-report.pdf"})
