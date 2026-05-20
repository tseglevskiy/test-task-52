# Shop Requirements

## Tech constraints
- SQLite as the only database (no Postgres, no MySQL)
- DB file path must be configurable via environment variable (e.g. `DATABASE_PATH=/data/shop.db`)
- Runs in Docker (single container), listens on a configurable port
- No authentication required — all pages accessible without login
- No real payment processing — checkout just places an order
- Reset must complete in under 3 seconds: wipe all data and re-seed from scratch given an integer seed

## Pages / routes
- Product listing page with category filter (`?category=Electronics`) and text search (`?q=cable`)
- Sorting on listing page by price ascending/descending
- Pagination on listing page (≥10 products per page)
- Product detail page with: name, SKU, price, category, stock count, quantity selector, "Add to Cart" button
- Cart page: line items with quantity + unit price, subtotal, coupon code input field + "Apply" button, discount line if coupon applied, total, "Proceed to Checkout" button
- Checkout page: shipping address form (name, street, city, state, zip), order summary, "Place Order" button
- Order confirmation page after successful checkout (shows order ID)
- Order list page (`/orders`) showing all orders with: order ID, date, status, total
- Order detail page with line items and a "Cancel Order" button (only shown if status is `placed`)

## Data model (minimum)
- Products: id, sku, name, category, price, stock
- Coupons: code, discount_percent, active flag
- Orders: id, created_at (integer epoch), status (`placed` / `cancelled`), shipping_address, total
- Order items: order_id, product_id, quantity, unit_price

## Verifier-friendly internals
- An internal API endpoint `GET /api/db-state` that returns raw JSON: all orders, order items, products, coupons — no HTML, no auth. Used by test verifiers only.
- DB file is volume-mounted and readable directly via `sqlite3` from outside the container

## Realistic UI (for agent difficulty)
- Site-wide header with: site name, category nav links, cart icon with item count badge
- Breadcrumbs on product detail page
- "Out of stock" state on products with stock=0 (add to cart disabled)
- At least 3 "related products" shown on product detail page (same category)
- Coupon error message if code is invalid
- Order status badge on order list and detail pages

## Explicitly out of scope
- User login / registration / sessions
- Payment forms or card numbers
- CSS styling beyond basic HTML structure
- Product images
- Email notifications
