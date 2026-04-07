"""
db/setup.py
-----------
Connects to TimescaleDB and applies the schema, retention policies,
and seeds example instruments.

Run with:  python -m db.setup
"""

import os
import sys
import pathlib

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", 5432)),
    "dbname":   os.getenv("POSTGRES_DB",       "marketdata"),
    "user":     os.getenv("POSTGRES_USER",     "marketdata"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def apply_schema(conn):
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(schema_sql)
    conn.commit()
    print("Schema applied.")


def apply_retention_policies(conn):
    policies = [
        ("trades",     int(os.getenv("TICK_RETENTION_DAYS",      "90"))),
        ("order_book", int(os.getenv("ORDERBOOK_RETENTION_DAYS", "30"))),
        ("ohlcv_bars", int(os.getenv("OHLCV_RETENTION_DAYS",     "0"))),
    ]
    with conn.cursor() as cur:
        for table, days in policies:
            if days > 0:
                cur.execute(
                    "SELECT add_retention_policy(%s, INTERVAL %s, if_not_exists => TRUE)",
                    (table, f"{days} days"),
                )
                print(f"Retention policy: {table} -> {days} days")
            else:
                print(f"Retention policy: {table} -> disabled")
    conn.commit()


def seed_instruments(conn):
    instruments = [
        ("CL",   "Crude Oil WTI Front Month", "futures", "NYMEX", "USD"),
        ("GC",   "Gold Front Month",           "futures", "COMEX", "USD"),
        ("ES",   "E-mini S&P 500",             "futures", "CME",   "USD"),
        ("AAPL", "Apple Inc.",                 "equity",  "NASDAQ","USD"),
        ("SPY",  "SPDR S&P 500 ETF",           "equity",  "NYSE",  "USD"),
    ]
    futures_meta = {
        "CL": ("CL", 1000,  0.01,  10.0,  True),
        "GC": ("GC", 100,   0.10,  10.0,  True),
        "ES": ("ES", 50,    0.25,  12.5,  True),
    }
    equity_meta = {
        "AAPL": ("Technology", "mega"),
        "SPY":  ("Index Fund", "mega"),
    }

    with conn.cursor() as cur:
        for sym, name, ac, exch, ccy in instruments:
            cur.execute(
                """
                INSERT INTO instruments (symbol, name, asset_class, exchange, currency)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (symbol, exchange) DO NOTHING
                RETURNING instrument_id
                """,
                (sym, name, ac, exch, ccy),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "SELECT instrument_id FROM instruments WHERE symbol=%s AND exchange=%s",
                    (sym, exch),
                )
                row = cur.fetchone()
            inst_id = row[0]

            if sym in futures_meta:
                underlying, size, tick, tick_val, front = futures_meta[sym]
                cur.execute(
                    """
                    INSERT INTO futures_metadata
                        (instrument_id, underlying_symbol, contract_size,
                         tick_size, tick_value, is_front_month)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (instrument_id) DO NOTHING
                    """,
                    (inst_id, underlying, size, tick, tick_val, front),
                )
            elif sym in equity_meta:
                sector, cap = equity_meta[sym]
                cur.execute(
                    """
                    INSERT INTO equity_metadata
                        (instrument_id, sector, market_cap_category)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (instrument_id) DO NOTHING
                    """,
                    (inst_id, sector, cap),
                )
    conn.commit()
    print(f"Seeded {len(instruments)} example instruments.")


def main():
    print(f"Connecting to {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']} ...")
    try:
        conn = get_connection()
    except psycopg2.OperationalError as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        apply_schema(conn)
        apply_retention_policies(conn)
        seed_instruments(conn)
    finally:
        conn.close()

    print("Setup complete.")


if __name__ == "__main__":
    main()
