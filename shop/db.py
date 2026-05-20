"""
shop/db.py — SQLite schema constant + connection helpers.

No ORM, no SQLAlchemy — plain sqlite3 stdlib only.
"""
import sqlite3


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id          TEXT PRIMARY KEY,
    sku         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       REAL NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS coupons (
    id              TEXT PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    discount_pct    REAL NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS orders (
    id               TEXT PRIMARY KEY,
    created_at       INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'placed',
    shipping_address TEXT NOT NULL,
    coupon_code      TEXT,
    discount_pct     REAL NOT NULL DEFAULT 0.0,
    subtotal         REAL NOT NULL,
    total            REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id          TEXT PRIMARY KEY,
    order_id    TEXT NOT NULL REFERENCES orders(id),
    product_id  TEXT NOT NULL REFERENCES products(id),
    sku         TEXT NOT NULL,
    name        TEXT NOT NULL,
    quantity    INTEGER NOT NULL,
    unit_price  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cart_items (
    session_id  TEXT NOT NULL,
    product_id  TEXT NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (session_id, product_id)
);

CREATE TABLE IF NOT EXISTS cart_meta (
    session_id   TEXT PRIMARY KEY,
    coupon_code  TEXT
);

CREATE TABLE IF NOT EXISTS shop_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def get_db(db_path: str) -> sqlite3.Connection:
    """
    Open and return a sqlite3.Connection configured for use:
      - row_factory = sqlite3.Row  (column access by name)
      - PRAGMA journal_mode=WAL    (better concurrent read performance)
      - PRAGMA foreign_keys=ON
    Caller is responsible for closing (use as context manager).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    """
    Idempotent schema initialisation.
    Safe to call on an empty file, a pre-seeded file, or a file from a previous episode.
    Never drops tables or deletes data — only CREATE TABLE IF NOT EXISTS.
    """
    with get_db(db_path) as conn:
        conn.executescript(CREATE_TABLES_SQL)
