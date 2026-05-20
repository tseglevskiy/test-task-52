"""
shop/app.py — Flask e-commerce application for ShopGym.

Usage:
  DATABASE_PATH=/tmp/shop.db LOG_PATH=/tmp/shop.jsonl python app.py

All state lives in the SQLite file; the Flask process holds zero in-memory
state between requests.  The cart is stored in cart_items (DB table), keyed
by a UUID4 session cookie.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from db import get_db, init_db
from seed import SeedConfig, RequiredProduct, RequiredCoupon, RequiredOrder, seed_database, get_db_snapshot

# ---------------------------------------------------------------------------
# US States (50 + DC) — used in checkout <select>
# ---------------------------------------------------------------------------
US_STATES: list[tuple[str, str]] = [
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
    ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"),
    ("DC", "District of Columbia"), ("DE", "Delaware"), ("FL", "Florida"),
    ("GA", "Georgia"), ("HI", "Hawaii"), ("ID", "Idaho"), ("IL", "Illinois"),
    ("IN", "Indiana"), ("IA", "Iowa"), ("KS", "Kansas"), ("KY", "Kentucky"),
    ("LA", "Louisiana"), ("ME", "Maine"), ("MD", "Maryland"),
    ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"),
    ("MS", "Mississippi"), ("MO", "Missouri"), ("MT", "Montana"),
    ("NE", "Nebraska"), ("NV", "Nevada"), ("NH", "New Hampshire"),
    ("NJ", "New Jersey"), ("NM", "New Mexico"), ("NY", "New York"),
    ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"),
    ("OK", "Oklahoma"), ("OR", "Oregon"), ("PA", "Pennsylvania"),
    ("RI", "Rhode Island"), ("SC", "South Carolina"), ("SD", "South Dakota"),
    ("TN", "Tennessee"), ("TX", "Texas"), ("UT", "Utah"), ("VT", "Vermont"),
    ("VA", "Virginia"), ("WA", "Washington"), ("WV", "West Virginia"),
    ("WI", "Wisconsin"), ("WY", "Wyoming"),
]

_US_STATE_ABBRS = {abbr for abbr, _ in US_STATES}

PAGE_SIZE = 12


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app(db_path: str, log_path: str | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = "shopgym-secret-not-for-production"
    app.config["DB_PATH"] = db_path
    app.config["LOG_PATH"] = log_path

    # Ensure schema exists (idempotent — safe on empty or pre-seeded file)
    init_db(db_path)

    # ------------------------------------------------------------------
    # Session cookie — UUID4 per browser session, stored in a plain cookie
    # ------------------------------------------------------------------

    @app.before_request
    def _load_session():
        sid = request.cookies.get("session_id")
        if sid:
            g.session_id = sid
            g.new_session = False
        else:
            g.session_id = str(uuid.uuid4())
            g.new_session = True

    @app.after_request
    def _set_session_cookie(response):
        if g.get("new_session"):
            response.set_cookie(
                "session_id",
                g.session_id,
                max_age=86400 * 30,
                httponly=True,
                samesite="Lax",
            )
        return response

    # ------------------------------------------------------------------
    # Context processor — injects categories + cart_count into every template
    # ------------------------------------------------------------------

    @app.context_processor
    def _inject_globals():
        db_path_ = app.config["DB_PATH"]
        cats = []
        cart_count = 0
        try:
            with get_db(db_path_) as conn:
                cats = [
                    row[0]
                    for row in conn.execute(
                        "SELECT category FROM products GROUP BY category ORDER BY MIN(sort_order)"
                    )
                ]
        except Exception:
            cats = []
        try:
            with get_db(db_path_) as conn:
                cart_count_row = conn.execute(
                    "SELECT COALESCE(SUM(quantity), 0) FROM cart_items WHERE session_id=?",
                    (g.session_id,),
                ).fetchone()
                cart_count = int(cart_count_row[0]) if cart_count_row else 0
        except Exception:
            cart_count = 0
        return {"categories": cats, "cart_count": cart_count}

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _get_cart_info(conn, session_id: str) -> dict:
        """Build complete cart state from DB."""
        rows = conn.execute(
            """
            SELECT ci.product_id, ci.quantity, p.name, p.sku, p.price AS unit_price
            FROM cart_items ci
            JOIN products p ON p.id = ci.product_id
            WHERE ci.session_id = ?
            ORDER BY p.sort_order
            """,
            (session_id,),
        ).fetchall()

        items = [dict(r) for r in rows]

        meta = conn.execute(
            "SELECT coupon_code FROM cart_meta WHERE session_id=?", (session_id,)
        ).fetchone()
        coupon_code = meta["coupon_code"] if meta else None

        discount_pct = 0.0
        if coupon_code:
            coupon = conn.execute(
                "SELECT discount_pct, active FROM coupons WHERE UPPER(code)=UPPER(?)",
                (coupon_code,),
            ).fetchone()
            if coupon and coupon["active"]:
                discount_pct = coupon["discount_pct"]
            else:
                # Coupon became invalid (e.g. after a reset); clear it
                conn.execute("DELETE FROM cart_meta WHERE session_id=?", (session_id,))
                coupon_code = None
                discount_pct = 0.0

        subtotal = round(sum(it["unit_price"] * it["quantity"] for it in items), 2)
        discount_amount = round(subtotal * discount_pct / 100.0, 2)
        total = round(subtotal - discount_amount, 2)

        return {
            "line_items": items,
            "coupon_code": coupon_code,
            "discount_pct": discount_pct,
            "discount_amount": discount_amount,
            "subtotal": subtotal,
            "total": total,
            "n_items": sum(it["quantity"] for it in items),
        }

    def _format_date(epoch: int) -> str:
        """Unix epoch → human-readable date string."""
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return dt.strftime("%B %-d, %Y")

    def _get_related_products(conn, product_id: str, category: str, n: int = 3) -> list:
        """
        Return up to n related products.
        Cyclic within same category by sort_order, then fill from other categories.
        """
        same_cat = conn.execute(
            "SELECT id, name, price, category, sort_order FROM products "
            "WHERE category=? AND id!=? ORDER BY sort_order",
            (category, product_id),
        ).fetchall()

        if len(same_cat) >= n:
            # Cyclic: find current product's position among ALL in category
            all_in_cat = conn.execute(
                "SELECT id FROM products WHERE category=? ORDER BY sort_order",
                (category,),
            ).fetchall()
            ids_in_cat = [r["id"] for r in all_in_cat]
            try:
                cur_pos = ids_in_cat.index(product_id)
            except ValueError:
                cur_pos = 0
            m = len(same_cat)
            # same_cat already excludes current; map cyclic positions
            same_cat_ids = [r["id"] for r in same_cat]
            result_ids = []
            for offset in range(1, n + 1):
                idx = (cur_pos + offset - 1) % m
                result_ids.append(same_cat_ids[idx])
            return [dict(r) for r in same_cat if r["id"] in result_ids]

        # Not enough in same category — fill from others
        related = list(same_cat)
        if len(related) < n:
            others = conn.execute(
                "SELECT id, name, price, category, sort_order FROM products "
                "WHERE category!=? AND id!=? ORDER BY sort_order LIMIT ?",
                (category, product_id, n - len(related)),
            ).fetchall()
            related.extend(others)
        return [dict(r) for r in related[:n]]

    def _log_event(session_id: str, method: str, path: str,
                   params: dict, result: dict) -> None:
        """Append one JSON line to the JSONL event log. Never raises."""
        log_path_ = app.config.get("LOG_PATH")
        if not log_path_:
            return
        record = {
            "ts": time.time(),
            "session_id": session_id,
            "method": method,
            "path": path,
            "params": params,
            "result": result,
        }
        try:
            with open(log_path_, "a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as exc:
            print(f"[shopgym] log write failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Routes — storefront
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        db_path_ = app.config["DB_PATH"]
        active_category = request.args.get("category", "").strip()
        q = request.args.get("q", "").strip()
        sort = request.args.get("sort", "default")
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1

        with get_db(db_path_) as conn:
            # Build WHERE clause
            conditions = []
            bindings: list = []
            if active_category:
                conditions.append("UPPER(category)=UPPER(?)")
                bindings.append(active_category)
            if q:
                conditions.append("(UPPER(name) LIKE UPPER(?) OR UPPER(description) LIKE UPPER(?))")
                bindings.extend([f"%{q}%", f"%{q}%"])

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            # Sort
            if sort == "price_asc":
                order = "ORDER BY price ASC"
            elif sort == "price_desc":
                order = "ORDER BY price DESC"
            else:
                order = "ORDER BY sort_order ASC"

            total_products = conn.execute(
                f"SELECT COUNT(*) FROM products {where}", bindings
            ).fetchone()[0]

            total_pages = max(1, math.ceil(total_products / PAGE_SIZE))
            page = min(page, total_pages)
            offset = (page - 1) * PAGE_SIZE

            products = conn.execute(
                f"SELECT id, sku, name, category, price, sort_order "
                f"FROM products {where} {order} LIMIT ? OFFSET ?",
                bindings + [PAGE_SIZE, offset],
            ).fetchall()

        _log_event(g.session_id, "GET", "/", {
            "category": active_category, "q": q, "sort": sort, "page": page,
        }, {"n_products": len(products), "n_pages": total_pages, "current_page": page})

        return render_template(
            "index.html",
            products=products,
            active_category=active_category,
            q=q,
            sort=sort,
            page=page,
            total_pages=total_pages,
            total_products=total_products,
        )

    @app.route("/product/<product_id>")
    def product_detail(product_id: str):
        db_path_ = app.config["DB_PATH"]
        with get_db(db_path_) as conn:
            product = conn.execute(
                "SELECT id, sku, name, description, category, price FROM products WHERE id=?",
                (product_id,),
            ).fetchone()
            if not product:
                abort(404)
            related = _get_related_products(conn, product_id, product["category"])

        _log_event(g.session_id, "GET", f"/product/{product_id}",
                   {"product_id": product_id},
                   {"sku": product["sku"], "name": product["name"],
                    "category": product["category"], "price": product["price"]})

        return render_template("product.html", product=dict(product), related=related)

    # ------------------------------------------------------------------
    # Routes — cart
    # ------------------------------------------------------------------

    @app.route("/cart")
    def cart():
        db_path_ = app.config["DB_PATH"]
        coupon_error = request.args.get("coupon_error", "")
        with get_db(db_path_) as conn:
            cart_info = _get_cart_info(conn, g.session_id)

        _log_event(g.session_id, "GET", "/cart", {},
                   {"n_items": cart_info["n_items"], "subtotal": cart_info["subtotal"],
                    "coupon_code": cart_info["coupon_code"], "total": cart_info["total"]})

        return render_template("cart.html", cart=cart_info, coupon_error=coupon_error)

    @app.route("/cart/add", methods=["POST"])
    def cart_add():
        db_path_ = app.config["DB_PATH"]
        product_id = request.form.get("product_id", "").strip()
        try:
            quantity = int(request.form.get("quantity", 1))
        except ValueError:
            quantity = 1
        quantity = max(1, quantity)

        with get_db(db_path_) as conn:
            product = conn.execute(
                "SELECT id FROM products WHERE id=?", (product_id,)
            ).fetchone()
            if not product:
                _log_event(g.session_id, "POST", "/cart/add",
                           {"product_id": product_id, "quantity": quantity},
                           {"status": "not_found", "cart_total_items": 0})
                abort(404)

            # Upsert: if already in cart, increment quantity
            existing = conn.execute(
                "SELECT quantity FROM cart_items WHERE session_id=? AND product_id=?",
                (g.session_id, product_id),
            ).fetchone()
            if existing:
                new_qty = existing["quantity"] + quantity
                conn.execute(
                    "UPDATE cart_items SET quantity=? WHERE session_id=? AND product_id=?",
                    (new_qty, g.session_id, product_id),
                )
            else:
                conn.execute(
                    "INSERT INTO cart_items (session_id, product_id, quantity) VALUES (?,?,?)",
                    (g.session_id, product_id, quantity),
                )

            total_items = conn.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM cart_items WHERE session_id=?",
                (g.session_id,),
            ).fetchone()[0]

        _log_event(g.session_id, "POST", "/cart/add",
                   {"product_id": product_id, "quantity": quantity},
                   {"status": "ok", "cart_total_items": total_items})

        return redirect(url_for("cart"))

    @app.route("/cart/update", methods=["POST"])
    def cart_update():
        db_path_ = app.config["DB_PATH"]
        product_id = request.form.get("product_id", "").strip()
        try:
            quantity = int(request.form.get("quantity", 1))
        except ValueError:
            return ("Invalid quantity", 400)

        if quantity < 0:
            _log_event(g.session_id, "POST", "/cart/update",
                       {"product_id": product_id, "quantity": quantity},
                       {"status": "invalid_quantity"})
            return ("quantity must be >= 0", 400)

        with get_db(db_path_) as conn:
            existing = conn.execute(
                "SELECT quantity FROM cart_items WHERE session_id=? AND product_id=?",
                (g.session_id, product_id),
            ).fetchone()
            if not existing:
                _log_event(g.session_id, "POST", "/cart/update",
                           {"product_id": product_id, "quantity": quantity},
                           {"status": "not_found"})
                return redirect(url_for("cart"))

            if quantity == 0:
                conn.execute(
                    "DELETE FROM cart_items WHERE session_id=? AND product_id=?",
                    (g.session_id, product_id),
                )
            else:
                conn.execute(
                    "UPDATE cart_items SET quantity=? WHERE session_id=? AND product_id=?",
                    (quantity, g.session_id, product_id),
                )

        _log_event(g.session_id, "POST", "/cart/update",
                   {"product_id": product_id, "quantity": quantity},
                   {"status": "ok"})
        return redirect(url_for("cart"))

    @app.route("/cart/remove", methods=["POST"])
    def cart_remove():
        db_path_ = app.config["DB_PATH"]
        product_id = request.form.get("product_id", "").strip()

        with get_db(db_path_) as conn:
            existing = conn.execute(
                "SELECT 1 FROM cart_items WHERE session_id=? AND product_id=?",
                (g.session_id, product_id),
            ).fetchone()
            status = "ok" if existing else "not_found"
            conn.execute(
                "DELETE FROM cart_items WHERE session_id=? AND product_id=?",
                (g.session_id, product_id),
            )

        _log_event(g.session_id, "POST", "/cart/remove",
                   {"product_id": product_id}, {"status": status})
        return redirect(url_for("cart"))

    @app.route("/cart/coupon", methods=["POST"])
    def cart_coupon():
        db_path_ = app.config["DB_PATH"]
        code = request.form.get("code", "").strip()

        if not code:
            # Remove applied coupon
            with get_db(db_path_) as conn:
                conn.execute("DELETE FROM cart_meta WHERE session_id=?", (g.session_id,))
            _log_event(g.session_id, "POST", "/cart/coupon",
                       {"code": ""}, {"status": "removed"})
            return redirect(url_for("cart"))

        with get_db(db_path_) as conn:
            coupon = conn.execute(
                "SELECT code, discount_pct, active FROM coupons WHERE UPPER(code)=UPPER(?)",
                (code,),
            ).fetchone()

            if not coupon:
                _log_event(g.session_id, "POST", "/cart/coupon",
                           {"code": code}, {"status": "invalid_code"})
                return redirect(url_for("cart", coupon_error=f"Coupon '{code}' not found."))

            if not coupon["active"]:
                _log_event(g.session_id, "POST", "/cart/coupon",
                           {"code": code}, {"status": "inactive"})
                return redirect(url_for("cart", coupon_error=f"Coupon '{code}' is no longer active."))

            # Valid — upsert into cart_meta
            conn.execute(
                "INSERT INTO cart_meta (session_id, coupon_code) VALUES (?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET coupon_code=excluded.coupon_code",
                (g.session_id, coupon["code"]),
            )

            cart_info = _get_cart_info(conn, g.session_id)

        _log_event(g.session_id, "POST", "/cart/coupon",
                   {"code": code},
                   {"status": "ok", "discount_pct": coupon["discount_pct"],
                    "discount_amount": cart_info["discount_amount"]})
        return redirect(url_for("cart"))

    # ------------------------------------------------------------------
    # Routes — checkout
    # ------------------------------------------------------------------

    @app.route("/checkout", methods=["GET"])
    def checkout_get():
        db_path_ = app.config["DB_PATH"]
        with get_db(db_path_) as conn:
            cart_info = _get_cart_info(conn, g.session_id)

        if not cart_info["line_items"]:
            flash("Your cart is empty.")
            return redirect(url_for("cart"))

        _log_event(g.session_id, "GET", "/checkout", {},
                   {"n_items": cart_info["n_items"], "subtotal": cart_info["subtotal"],
                    "total": cart_info["total"]})

        return render_template(
            "checkout.html",
            cart=cart_info,
            us_states=US_STATES,
            errors={},
            form={"name": "", "street": "", "city": "", "state": "", "zip": ""},
        )

    @app.route("/checkout", methods=["POST"])
    def checkout_post():
        db_path_ = app.config["DB_PATH"]

        # Collect form values
        form = {
            "name":   request.form.get("name", "").strip(),
            "street": request.form.get("street", "").strip(),
            "city":   request.form.get("city", "").strip(),
            "state":  request.form.get("state", "").strip(),
            "zip":    request.form.get("zip", "").strip(),
        }

        # Validate
        errors: dict[str, str] = {}
        if not form["name"]:
            errors["name"] = "Full name is required."
        if not form["street"]:
            errors["street"] = "Street address is required."
        if not form["city"]:
            errors["city"] = "City is required."
        if form["state"] not in _US_STATE_ABBRS:
            errors["state"] = "Please select a valid US state."
        if not re.fullmatch(r"\d{5}", form["zip"]):
            errors["zip"] = "ZIP code must be exactly 5 digits."

        with get_db(db_path_) as conn:
            cart_info = _get_cart_info(conn, g.session_id)

        if not cart_info["line_items"]:
            flash("Your cart is empty.")
            return redirect(url_for("cart"))

        if errors:
            _log_event(g.session_id, "POST", "/checkout", form,
                       {"status": "validation_error", "errors": errors})
            return render_template(
                "checkout.html",
                cart=cart_info,
                us_states=US_STATES,
                errors=errors,
                form=form,
            )

        # Place order
        shipping_address = f"{form['name']}\n{form['street']}\n{form['city']}, {form['state']} {form['zip']}"
        order_id = str(uuid.uuid4())

        with get_db(db_path_) as conn:
            # Read virtual clock
            meta_row = conn.execute(
                "SELECT value FROM shop_meta WHERE key='next_order_ts'"
            ).fetchone()
            if meta_row:
                created_at = int(meta_row["value"])
            else:
                # Fallback if shop_meta wasn't seeded (shouldn't happen after reset)
                created_at = 1_716_003_600

            cart_info = _get_cart_info(conn, g.session_id)

            conn.execute(
                "INSERT INTO orders "
                "(id, created_at, status, shipping_address, coupon_code, discount_pct, subtotal, total) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (order_id, created_at, "placed", shipping_address,
                 cart_info["coupon_code"], cart_info["discount_pct"],
                 cart_info["subtotal"], cart_info["total"]),
            )

            for item in cart_info["line_items"]:
                item_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO order_items "
                    "(id, order_id, product_id, sku, name, quantity, unit_price) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (item_id, order_id, item["product_id"], item["sku"],
                     item["name"], item["quantity"], item["unit_price"]),
                )

            # Advance virtual clock by 1 hour
            conn.execute(
                "INSERT INTO shop_meta (key, value) VALUES ('next_order_ts', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(created_at + 3600),),
            )

            # Clear cart
            conn.execute("DELETE FROM cart_items WHERE session_id=?", (g.session_id,))
            conn.execute("DELETE FROM cart_meta WHERE session_id=?", (g.session_id,))

        _log_event(g.session_id, "POST", "/checkout", form,
                   {"status": "ok", "order_id": order_id})

        return redirect(url_for("order_detail", order_id=order_id, confirmed=1))

    # ------------------------------------------------------------------
    # Routes — orders
    # ------------------------------------------------------------------

    @app.route("/order/<order_id>")
    def order_detail(order_id: str):
        db_path_ = app.config["DB_PATH"]
        with get_db(db_path_) as conn:
            order = conn.execute(
                "SELECT id, created_at, status, shipping_address, coupon_code, "
                "discount_pct, subtotal, total FROM orders WHERE id=?",
                (order_id,),
            ).fetchone()
            if not order:
                abort(404)
            items = conn.execute(
                "SELECT id, product_id, sku, name, quantity, unit_price "
                "FROM order_items WHERE order_id=?",
                (order_id,),
            ).fetchall()

        order_dict = dict(order)
        order_dict["date_str"] = _format_date(order["created_at"])
        confirmed = "confirmed" in request.args

        _log_event(g.session_id, "GET", f"/order/{order_id}",
                   {"order_id": order_id},
                   {"status": order["status"], "total": order["total"],
                    "n_items": len(items)})

        return render_template(
            "order.html",
            order=order_dict,
            items=[dict(i) for i in items],
            confirmed=confirmed,
        )

    @app.route("/orders")
    def orders_list():
        db_path_ = app.config["DB_PATH"]
        with get_db(db_path_) as conn:
            rows = conn.execute(
                "SELECT id, created_at, status, total FROM orders ORDER BY created_at DESC"
            ).fetchall()

        orders = []
        for r in rows:
            d = dict(r)
            d["date_str"] = _format_date(r["created_at"])
            orders.append(d)

        _log_event(g.session_id, "GET", "/orders", {}, {"n_orders": len(orders)})

        return render_template("orders.html", orders=orders)

    @app.route("/orders/<order_id>/cancel", methods=["POST"])
    def order_cancel(order_id: str):
        db_path_ = app.config["DB_PATH"]
        with get_db(db_path_) as conn:
            order = conn.execute(
                "SELECT id, status FROM orders WHERE id=?", (order_id,)
            ).fetchone()
            if not order:
                _log_event(g.session_id, "POST", f"/orders/{order_id}/cancel",
                           {"order_id": order_id}, {"status": "not_found"})
                abort(404)

            if order["status"] != "placed":
                _log_event(g.session_id, "POST", f"/orders/{order_id}/cancel",
                           {"order_id": order_id}, {"status": "already_cancelled"})
                flash("This order has already been cancelled.")
                return redirect(url_for("order_detail", order_id=order_id))

            conn.execute(
                "UPDATE orders SET status='cancelled' WHERE id=?", (order_id,)
            )

        _log_event(g.session_id, "POST", f"/orders/{order_id}/cancel",
                   {"order_id": order_id}, {"status": "ok"})

        flash("Order cancelled successfully.")
        return redirect(url_for("order_detail", order_id=order_id))

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    # ------------------------------------------------------------------
    # Routes — internal API (not exposed to agents)
    # ------------------------------------------------------------------

    @app.route("/api/health")
    def api_health():
        return jsonify({"status": "ok"})

    @app.route("/api/db-state")
    def api_db_state():
        db_path_ = app.config["DB_PATH"]
        snapshot = get_db_snapshot(db_path_)
        return jsonify(snapshot)

    @app.route("/api/reset", methods=["POST"])
    def api_reset():
        db_path_ = app.config["DB_PATH"]
        t0 = time.monotonic()

        # Accept bare ?seed=N (no body) or full JSON body
        seed_param = request.args.get("seed")
        body = request.get_json(silent=True) or {}

        if seed_param is not None and not body:
            seed = int(seed_param)
            config = SeedConfig(seed=seed)
        else:
            seed = body.get("seed")
            if seed is None:
                return jsonify({"error": "seed is required"}), 400
            seed = int(seed)

            def _parse_rp(d: dict) -> RequiredProduct:
                cat = d.pop("category")
                return RequiredProduct(category=cat, overrides=d)

            def _parse_rc(d: dict) -> RequiredCoupon:
                return RequiredCoupon(
                    code=d["code"],
                    discount_pct=float(d["discount_pct"]),
                    active=bool(d.get("active", True)),
                )

            def _parse_ro(d: dict) -> RequiredOrder:
                return RequiredOrder(status=d["status"])

            config = SeedConfig(
                seed=seed,
                base_ts=int(body.get("base_ts", 1_716_000_000)),
                n_categories=int(body.get("n_categories", 5)),
                n_products_per_category=int(body.get("n_products_per_category", 8)),
                required_products=[_parse_rp(dict(d)) for d in body.get("required_products", [])],
                required_coupons=[_parse_rc(d) for d in body.get("required_coupons", [])],
                required_orders=[_parse_ro(d) for d in body.get("required_orders", [])],
                n_filler_orders=int(body.get("n_filler_orders", 3)),
            )

        seed_database(db_path_, config)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return jsonify({"status": "ok", "seed": seed, "elapsed_ms": elapsed_ms})

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _db_path = os.environ.get("DATABASE_PATH", "/app/shop.db")
    _log_path = os.environ.get("LOG_PATH", None)
    _app = create_app(_db_path, _log_path)
    _app.run(host="0.0.0.0", port=5000, debug=False)
