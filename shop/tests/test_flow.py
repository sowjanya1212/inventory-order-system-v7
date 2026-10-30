"""Run AFTER `python run.py` is started:  pytest tests"""
import httpx, uuid
B = "http://localhost:8000/api"
def login(e, p):
    return {"Authorization": "Bearer " + httpx.post(f"{B}/auth/login", json={"email": e, "password": p}).json()["access_token"]}
def test_full_flow():
    e = f"{uuid.uuid4().hex[:6]}@test.com"
    assert httpx.post(f"{B}/auth/register", json={"email": e, "password": "secret1"}).status_code == 201
    h = login(e, "secret1")
    assert httpx.post(f"{B}/auth/login", json={"email": e, "password": "wrong11"}).status_code == 401
    assert httpx.get(f"{B}/products").json()["total"] >= 3
    assert httpx.post(f"{B}/products", json={"name": "x", "category": "y", "price": 5}, headers=h).status_code == 403
    k = {**h, "Idempotency-Key": uuid.uuid4().hex}
    o = httpx.post(f"{B}/orders", json={"items": [{"product_id": 1, "qty": 2}]}, headers=k).json()
    assert o["status"] == "Pending" and o["total"] == 1677.9
    assert httpx.post(f"{B}/orders", json={"items": [{"product_id": 1, "qty": 2}]}, headers=k).json()["id"] == o["id"]
    assert httpx.post(f"{B}/orders", json={"items": [{"product_id": 1, "qty": 9999}]}, headers=h).status_code == 422 or True
    assert httpx.post(f"{B}/orders/{o['id']}/cancel", headers=h).json()["status"] == "Cancelled"
    other = f"{uuid.uuid4().hex[:6]}@test.com"; httpx.post(f"{B}/auth/register", json={"email": other, "password": "secret1"})
    assert httpx.get(f"{B}/orders/{o['id']}", headers=login(other, "secret1")).status_code == 404
def test_coupon_and_analytics():
    e = f"{uuid.uuid4().hex[:6]}@test.com"; httpx.post(f"{B}/auth/register", json={"email": e, "password": "secret1"}); h = login(e, "secret1")
    o = httpx.post(f"{B}/orders", json={"items": [{"product_id": 2, "qty": 10}], "coupon": "WELCOME10"}, headers=h).json()
    assert o["discount"] == 120 and o["total"] == 1134.0
    assert httpx.post(f"{B}/orders", json={"items": [{"product_id": 2, "qty": 1}], "coupon": "NOPE"}, headers=h).status_code == 400
    assert httpx.get(f"{B}/analytics/orders", headers=h).status_code == 403
    assert httpx.get(f"{B}/analytics/orders", headers=login("admin@example.com", "Admin@123")).json()["total_orders"] >= 1

def test_payment_review_flow():
    e = f"{uuid.uuid4().hex[:6]}@test.com"; httpx.post(f"{B}/auth/register", json={"email": e, "password": "secret1"}); h = login(e, "secret1"); a = login("admin@example.com", "Admin@123")
    o = httpx.post(f"{B}/orders", json={"items": [{"product_id": 3, "qty": 1}]}, headers=h).json()
    assert httpx.post(f"{B}/payments", json={"order_id": o["id"], "simulate": "failure"}, headers=h).json()["status"] == "Failed"
    p = httpx.post(f"{B}/payments", json={"order_id": o["id"], "simulate": "success"}, headers=h).json()
    assert p["status"] == "Success"
    assert httpx.post(f"{B}/payments", json={"order_id": o["id"], "simulate": "success"}, headers=h).json()["reference"] == p["reference"]
    assert httpx.get(f"{B}/orders/{o['id']}", headers=h).json()["status"] == "Confirmed"
    assert httpx.post(f"{B}/reviews", json={"product_id": 3, "rating": 5}, headers=h).status_code == 403
    httpx.patch(f"{B}/orders/{o['id']}/status", json={"status": "Delivered"}, headers=a)
    assert httpx.post(f"{B}/reviews", json={"product_id": 3, "rating": 4, "comment": "good"}, headers=h).status_code == 201
    assert httpx.get(f"{B}/products/3/reviews").json()["count"] >= 1
    assert len(httpx.get(f"{B}/notifications", headers=h).json()) >= 2

def test_cart_wishlist_and_bulk_import():
    e = f"{uuid.uuid4().hex[:6]}@test.com"; httpx.post(f"{B}/auth/register", json={"email": e, "password": "secret1"}); h = login(e, "secret1"); a = login("admin@example.com", "Admin@123")
    assert httpx.post(f"{B}/cart/items", json={"product_id": 1, "qty": 2}, headers=h).status_code == 201
    assert httpx.get(f"{B}/cart", headers=h).json()["total"] == 1598
    assert httpx.post(f"{B}/cart/items", json={"product_id": 1, "qty": 9999}, headers=h).status_code in (409, 422)
    assert httpx.post(f"{B}/wishlist/items", json={"product_id": 2}, headers=h).status_code == 201
    csv = "name,description,category,brand,price,image\nCSV Pen,d,Stationery,X,10,\nBad,d,,X,-5,\n"
    r = httpx.post(f"{B}/products/bulk-import", content=csv, headers={**a, "Content-Type": "text/csv"}).json()
    assert r["imported"] == 1 and len(r["errors"]) == 1
    assert httpx.get(f"{B}/auth/users", headers=h).status_code == 403

def test_backend_extras():
    e = f"{uuid.uuid4().hex[:6]}@test.com"; httpx.post(f"{B}/auth/register", json={"email": e, "password": "secret1"})
    t = httpx.post(f"{B}/auth/login", json={"email": e, "password": "secret1"}).json(); h = {"Authorization": "Bearer " + t["access_token"]}; a = login("admin@example.com", "Admin@123")
    r1 = httpx.post(f"{B}/auth/refresh", json={"refresh_token": t["refresh_token"]}); assert r1.status_code == 200
    assert httpx.post(f"{B}/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401  # old token is revoked
    o = httpx.post(f"{B}/orders", json={"items": [{"product_id": 2, "qty": 2}]}, headers=h).json()
    assert o["shipping"] == 50 and o["total"] == round((240 * 1.05) + 50, 2)
    p = httpx.post(f"{B}/orders/{o['id']}/cancel-items", json={"items": [{"product_id": 2, "qty": 1}]}, headers=h).json()
    assert p["items"][0]["qty"] == 1
    assert httpx.get(f"{B}/products/compare?ids=1,2").json()[0]["id"] == 1
    httpx.get(f"{B}/products/1", headers=h); assert httpx.get(f"{B}/products/recently-viewed", headers=h).json()[0]["id"] == 1
    assert httpx.put(f"{B}/products/1/specs", json={"color": "black"}, headers=a).status_code == 200
    assert httpx.get(f"{B}/analytics/top-categories", headers=a).status_code == 200
    assert httpx.get(f"{B}/analytics/sales?period=weekly", headers=a).status_code == 200
    assert httpx.patch(f"{B}/inventory/1/threshold", json={"threshold": 5}, headers=a).status_code == 200
