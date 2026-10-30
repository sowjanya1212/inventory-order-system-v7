# Inventory & Order Management (core version)
Services: gateway:8000 (also serves the React UI), auth:8001, products:8002, inventory:8003, orders:8004, coupons:8005, returns:8006, analytics:8007, payments:8008, notifications:8009, reviews:8010, cart/wishlist:8011. Each has its own SQLite file.
## Run (no Docker)
1. Install Python 3.10+  2. `pip install -r requirements.txt`  3. `python run.py`  4. Open http://localhost:8000
Admin login: admin@example.com / Admin@123. Stop: Ctrl+C. Swagger: http://localhost:8001/docs (each service has /docs).
## Run (Docker)
`docker compose up --build` then open http://localhost:8000. Stop: `docker compose down`.
## Tests
Start the app, then in a 2nd terminal: `pytest tests`
## Troubleshooting
Port busy -> close old terminals. Reset data -> stop app, delete the *.db files inside each service folder.
## Not included (extensions)
Redis, email sending, partial order cancellation, shipping charges, product specs/comparison, outbox pattern, PDF reports.

If you ran an older version, delete all *.db files first (orders table changed).

## Authentication
Login once (auth service). The gateway verifies the token one time and passes the user to the other services. Services on ports 8001-8007 are internal: do not expose them. In their Swagger pages, if an endpoint needs a user, fill header X-User-Id (e.g. 1) and X-User-Role (e.g. admin).

## Payments, events, reviews
Orders start as Pending. A successful simulated payment confirms the order (failed payment keeps it Pending so the customer can retry). Events (order created/paid/shipped/delivered/cancelled, payment failed, low stock) go through RabbitMQ when RABBITMQ_URL is set (Docker: management UI http://localhost:15672, guest/guest); locally they are sent by direct HTTP. Delete old *.db files when upgrading.

## Postman
Import postman/inventory-order-system.postman_collection.json, then run the collection (Runner) with the app running. It has success and failure cases.
## CSV import
POST /products/bulk-import (admin) with CSV text: name,description,category,brand,price,image

## Backend additions (v7)
Refresh tokens are stored hashed and rotated (reuse is rejected, POST /auth/logout revokes). Outbox pattern in events.py (events saved first, resent later). Orders: shipping (Rs 50, free from Rs 500), partial cancellation (POST /orders/{id}/cancel-items), payment marked Refunded on cancel/return refund. Returns accept item quantities. Inventory: per-product low-stock threshold (PATCH /inventory/{id}/threshold), movement/audit report (GET /inventory/report/movements). Products: specs (PUT /products/{id}/specs), compare (GET /products/compare?ids=1,2), recently viewed, min_rating and in_stock filters. Analytics: weekly sales, top categories, PDF report (GET /analytics/report.pdf). Redis (optional, set REDIS_URL; Docker enables it): login rate limit and 30-second product list cache.
Upgrading: old .db files keep working (new columns are added automatically); delete them for a clean start.
