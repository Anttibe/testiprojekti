"""
db/ingest.py
------------
Bulk ingestion helpers for OHLCV bars, trades, and order book data.

Example usage:
    from db.ingest import get_connection, ingest_ohlcv_bars
    from datetime import datetime, timezone

    bars = [
        {
            "instrument_id": 1,
            "timestamp": datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            "timeframe": "1m",
            "open": 4750.25, "high": 4751.00,
            "low": 4749.75, "close": 4750.50,
            "volume": 1234,
        },
    ]
    with get_connection() as conn:
        n = ingest_ohlcv_bars(conn, bars)
        print(f"Inserted {n} bars")
"""

import os
from typing import Any, Dict, List

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        dbname=os.getenv("POSTGRES_DB",       "marketdata"),
        user=os.getenv("POSTGRES_USER",     "marketdata"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )


# ------------------------------------------------------------------ #
# OHLCV
# ------------------------------------------------------------------ #

def ingest_ohlcv_bars(
    conn,
    rows: List[Dict[str, Any]],
    upsert: bool = True,
) -> int:
    """
    Bulk insert/upsert OHLCV bars.

    Required keys per row: instrument_id, timestamp, timeframe,
                           open, high, low, close, volume
    Optional keys:         vwap, trade_count

    upsert=True  → update existing rows on conflict
    upsert=False → skip existing rows on conflict
    """
    if not rows:
        return 0

    data = [
        (
            r["instrument_id"], r["timestamp"], r["timeframe"],
            r["open"], r["high"], r["low"], r["close"], r["volume"],
            r.get("vwap"), r.get("trade_count"),
        )
        for r in rows
    ]

    conflict = (
        """
        ON CONFLICT (instrument_id, timeframe, timestamp) DO UPDATE SET
            open        = EXCLUDED.open,
            high        = EXCLUDED.high,
            low         = EXCLUDED.low,
            close       = EXCLUDED.close,
            volume      = EXCLUDED.volume,
            vwap        = EXCLUDED.vwap,
            trade_count = EXCLUDED.trade_count
        """
        if upsert
        else "ON CONFLICT (instrument_id, timeframe, timestamp) DO NOTHING"
    )

    sql = f"""
        INSERT INTO ohlcv_bars
            (instrument_id, timestamp, timeframe,
             open, high, low, close, volume, vwap, trade_count)
        VALUES %s
        {conflict}
    """

    with conn.cursor() as cur:
        execute_values(cur, sql, data, page_size=1000)
        count = cur.rowcount
    conn.commit()
    return count


# ------------------------------------------------------------------ #
# Trades / Ticks
# ------------------------------------------------------------------ #

def ingest_trades(conn, rows: List[Dict[str, Any]]) -> int:
    """
    Bulk insert trade/tick records.

    Required keys: instrument_id, timestamp, price, size
    Optional keys: side ('buy'/'sell'/'unknown'), trade_id, conditions (list of str)

    Rows without a trade_id get a random UUID (via DB default).
    """
    if not rows:
        return 0

    data = [
        (
            r["instrument_id"], r["timestamp"],
            r["price"], r["size"],
            r.get("side", "unknown"),
            r.get("trade_id"),           # None → DB generates UUID
            r.get("conditions"),
        )
        for r in rows
    ]

    sql = """
        INSERT INTO trades
            (instrument_id, timestamp, price, size, side, trade_id, conditions)
        VALUES %s
        ON CONFLICT (instrument_id, timestamp, trade_id) DO NOTHING
    """

    with conn.cursor() as cur:
        execute_values(cur, sql, data, page_size=5000)
        count = cur.rowcount
    conn.commit()
    return count


# ------------------------------------------------------------------ #
# Order Book / Quotes
# ------------------------------------------------------------------ #

def ingest_order_book(conn, rows: List[Dict[str, Any]]) -> int:
    """
    Bulk insert order book snapshots or BBO quotes.

    Required keys: instrument_id, timestamp, bid_price, bid_size,
                   ask_price, ask_size
    Optional keys: depth_level (default 0 = BBO), exchange_seq
    """
    if not rows:
        return 0

    data = [
        (
            r["instrument_id"], r["timestamp"],
            r.get("depth_level", 0),
            r.get("bid_price"), r.get("bid_size"),
            r.get("ask_price"), r.get("ask_size"),
            r.get("exchange_seq"),
        )
        for r in rows
    ]

    sql = """
        INSERT INTO order_book
            (instrument_id, timestamp, depth_level,
             bid_price, bid_size, ask_price, ask_size, exchange_seq)
        VALUES %s
        ON CONFLICT (instrument_id, timestamp, depth_level) DO UPDATE SET
            bid_price    = EXCLUDED.bid_price,
            bid_size     = EXCLUDED.bid_size,
            ask_price    = EXCLUDED.ask_price,
            ask_size     = EXCLUDED.ask_size,
            exchange_seq = EXCLUDED.exchange_seq
    """

    with conn.cursor() as cur:
        execute_values(cur, sql, data, page_size=5000)
        count = cur.rowcount
    conn.commit()
    return count
