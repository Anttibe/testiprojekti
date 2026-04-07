-- ============================================================
-- db/schema.sql
-- TimescaleDB Market Data Schema
-- ============================================================

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================
-- INSTRUMENTS
-- ============================================================

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id   SERIAL          PRIMARY KEY,
    symbol          TEXT            NOT NULL,
    name            TEXT,
    asset_class     TEXT            NOT NULL CHECK (asset_class IN ('futures', 'equity')),
    exchange        TEXT            NOT NULL,
    currency        TEXT            NOT NULL DEFAULT 'USD',
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, exchange)
);

-- Futures-specific metadata
CREATE TABLE IF NOT EXISTS futures_metadata (
    instrument_id       INTEGER         PRIMARY KEY REFERENCES instruments(instrument_id) ON DELETE CASCADE,
    underlying_symbol   TEXT            NOT NULL,
    contract_size       NUMERIC(18, 6)  NOT NULL,
    tick_size           NUMERIC(18, 8)  NOT NULL,
    tick_value          NUMERIC(18, 6),
    expiry_date         DATE,
    is_front_month      BOOLEAN         NOT NULL DEFAULT FALSE,
    contract_month      TEXT,
    settlement_type     TEXT            CHECK (settlement_type IN ('cash', 'physical'))
);

-- Equity-specific metadata
CREATE TABLE IF NOT EXISTS equity_metadata (
    instrument_id       INTEGER         PRIMARY KEY REFERENCES instruments(instrument_id) ON DELETE CASCADE,
    sector              TEXT,
    industry            TEXT,
    market_cap_category TEXT            CHECK (market_cap_category IN ('nano', 'micro', 'small', 'mid', 'large', 'mega')),
    isin                TEXT,
    cusip               TEXT
);

CREATE INDEX IF NOT EXISTS idx_instruments_symbol      ON instruments (symbol);
CREATE INDEX IF NOT EXISTS idx_instruments_asset_class ON instruments (asset_class);
CREATE INDEX IF NOT EXISTS idx_instruments_exchange    ON instruments (exchange);
CREATE INDEX IF NOT EXISTS idx_futures_underlying      ON futures_metadata (underlying_symbol);
CREATE INDEX IF NOT EXISTS idx_futures_front_month     ON futures_metadata (is_front_month) WHERE is_front_month = TRUE;

-- ============================================================
-- OHLCV BARS
-- ============================================================

CREATE TABLE IF NOT EXISTS ohlcv_bars (
    instrument_id   INTEGER         NOT NULL REFERENCES instruments(instrument_id),
    timestamp       TIMESTAMPTZ     NOT NULL,
    timeframe       TEXT            NOT NULL,   -- '1m', '5m', '15m', '1h', '4h', '1d', '1w'
    open            NUMERIC(18, 8)  NOT NULL,
    high            NUMERIC(18, 8)  NOT NULL,
    low             NUMERIC(18, 8)  NOT NULL,
    close           NUMERIC(18, 8)  NOT NULL,
    volume          NUMERIC(24, 6)  NOT NULL,
    vwap            NUMERIC(18, 8),
    trade_count     INTEGER,
    PRIMARY KEY (instrument_id, timeframe, timestamp)
);

SELECT create_hypertable(
    'ohlcv_bars',
    'timestamp',
    chunk_time_interval => INTERVAL '1 week',
    if_not_exists => TRUE
);

ALTER TABLE ohlcv_bars SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'timestamp DESC',
    timescaledb.compress_segmentby = 'instrument_id, timeframe'
);
SELECT add_compression_policy('ohlcv_bars', INTERVAL '30 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_ohlcv_instrument_tf_time
    ON ohlcv_bars (instrument_id, timeframe, timestamp DESC);

-- ============================================================
-- CONTINUOUS AGGREGATE: Daily bars from 1-minute bars
-- ============================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_daily
WITH (timescaledb.continuous) AS
SELECT
    instrument_id,
    time_bucket('1 day', timestamp)                         AS bucket,
    first(open,  timestamp)                                 AS open,
    max(high)                                               AS high,
    min(low)                                                AS low,
    last(close,  timestamp)                                 AS close,
    sum(volume)                                             AS volume,
    sum(volume * vwap) / NULLIF(sum(volume), 0)             AS vwap,
    sum(trade_count)                                        AS trade_count
FROM ohlcv_bars
WHERE timeframe = '1m'
GROUP BY instrument_id, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'ohlcv_daily',
    start_offset      => INTERVAL '2 days',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => TRUE
);

ALTER MATERIALIZED VIEW ohlcv_daily SET (timescaledb.compress = TRUE);
SELECT add_compression_policy('ohlcv_daily', INTERVAL '7 days', if_not_exists => TRUE);

-- ============================================================
-- TRADES / TICKS
-- ============================================================

CREATE TABLE IF NOT EXISTS trades (
    instrument_id   INTEGER         NOT NULL REFERENCES instruments(instrument_id),
    timestamp       TIMESTAMPTZ     NOT NULL,
    price           NUMERIC(18, 8)  NOT NULL,
    size            NUMERIC(24, 6)  NOT NULL,
    side            TEXT            CHECK (side IN ('buy', 'sell', 'unknown')),
    trade_id        TEXT            NOT NULL DEFAULT gen_random_uuid()::TEXT,
    conditions      TEXT[],
    PRIMARY KEY (instrument_id, timestamp, trade_id)
);

SELECT create_hypertable(
    'trades',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

ALTER TABLE trades SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'timestamp DESC',
    timescaledb.compress_segmentby = 'instrument_id'
);
SELECT add_compression_policy('trades', INTERVAL '7 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_trades_instrument_time
    ON trades (instrument_id, timestamp DESC);

-- ============================================================
-- ORDER BOOK / QUOTES
-- depth_level: 0 = BBO, 1..N = full book levels
-- ============================================================

CREATE TABLE IF NOT EXISTS order_book (
    instrument_id   INTEGER         NOT NULL REFERENCES instruments(instrument_id),
    timestamp       TIMESTAMPTZ     NOT NULL,
    depth_level     SMALLINT        NOT NULL DEFAULT 0,
    bid_price       NUMERIC(18, 8),
    bid_size        NUMERIC(24, 6),
    ask_price       NUMERIC(18, 8),
    ask_size        NUMERIC(24, 6),
    exchange_seq    BIGINT,
    PRIMARY KEY (instrument_id, timestamp, depth_level)
);

SELECT create_hypertable(
    'order_book',
    'timestamp',
    chunk_time_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

ALTER TABLE order_book SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'timestamp DESC',
    timescaledb.compress_segmentby = 'instrument_id, depth_level'
);
SELECT add_compression_policy('order_book', INTERVAL '1 day', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_orderbook_instrument_time
    ON order_book (instrument_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_orderbook_bbo
    ON order_book (instrument_id, timestamp DESC)
    WHERE depth_level = 0;
