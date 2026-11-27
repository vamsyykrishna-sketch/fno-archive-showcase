-- fno-archive schema
-- Target: fno_archive database (PostgreSQL 17 + TimescaleDB 2.29)

-- ============================================================
-- instruments: underlying-level metadata (NIFTY, BANKNIFTY, CRUDEOIL, ...)
-- ============================================================
CREATE TABLE instruments (
    instrument_id   BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL UNIQUE,          -- e.g. 'NIFTY', 'CRUDEOIL'
    exchange        TEXT NOT NULL,                 -- 'NSE', 'MCX', 'BSE'
    segment         TEXT NOT NULL,                 -- 'INDEX', 'EQUITY', 'COMMODITY'
    lot_size        INT NOT NULL,
    tick_size       NUMERIC(10,4) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- contracts: per-expiry/strike tradable option contract
-- (all current bots trade CE/PE only; no bot trades futures directly)
-- ============================================================
CREATE TABLE contracts (
    contract_id       BIGSERIAL PRIMARY KEY,
    instrument_id     BIGINT NOT NULL REFERENCES instruments(instrument_id),
    trading_symbol    TEXT NOT NULL UNIQUE,          -- e.g. 'NIFTY24DEC24500CE'
    exchange_token    TEXT,                          -- broker-specific token (AngelOne/Fyers etc.)
    expiry_date       DATE NOT NULL,
    strike_price      NUMERIC(12,2) NOT NULL,
    option_type       TEXT NOT NULL CHECK (option_type IN ('CE', 'PE')),
    lot_size          INT NOT NULL,
    first_traded_date DATE,
    last_traded_date  DATE,                          -- expiry/delisting date; backfill boundary
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (instrument_id, expiry_date, strike_price, option_type)
);

CREATE INDEX idx_contracts_instrument_expiry ON contracts (instrument_id, expiry_date);
CREATE INDEX idx_contracts_last_traded_date ON contracts (last_traded_date);

-- ============================================================
-- ohlcv_1min: TimescaleDB hypertable, 1-day chunks, compressed after 30 days
-- ============================================================
CREATE TABLE ohlcv_1min (
    time         TIMESTAMPTZ NOT NULL,
    contract_id  BIGINT NOT NULL REFERENCES contracts(contract_id),
    open         NUMERIC(12,2) NOT NULL,
    high         NUMERIC(12,2) NOT NULL,
    low          NUMERIC(12,2) NOT NULL,
    close        NUMERIC(12,2) NOT NULL,
    volume       BIGINT NOT NULL DEFAULT 0,
    oi           BIGINT,                             -- open interest
    PRIMARY KEY (contract_id, time)
);

SELECT create_hypertable('ohlcv_1min', by_range('time', INTERVAL '1 day'));

ALTER TABLE ohlcv_1min SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'contract_id',
    timescaledb.compress_orderby   = 'time DESC'
);

SELECT add_compression_policy('ohlcv_1min', INTERVAL '30 days');

-- ============================================================
-- trading_calendar: authoritative session hours per exchange per date.
-- Backs expected_bars in collection_log so holiday/truncated-session
-- handling is an inspectable, correctable row rather than a constant
-- buried in collector code. NSE rows come from the same
-- holiday-master API + fallback list final_bot.py already uses;
-- MCX has no equivalent live source in this codebase yet and starts
-- from a manually maintained list.
-- ============================================================
CREATE TABLE trading_calendar (
    exchange        TEXT NOT NULL,          -- 'NSE', 'MCX'
    trade_date      DATE NOT NULL,
    is_trading_day  BOOLEAN NOT NULL,
    session_start   TIME,                   -- NULL when is_trading_day = false
    session_end     TIME,                   -- overridden per-date for truncated sessions
    notes           TEXT,                   -- e.g. 'Muhurat trading', 'Diwali Balipratipada'
    PRIMARY KEY (exchange, trade_date)
);

-- ============================================================
-- collection_log: daily collection status per contract
-- expired contracts can't be backfilled, so MISSING here is permanent
-- ============================================================
CREATE TABLE collection_log (
    contract_id    BIGINT NOT NULL REFERENCES contracts(contract_id),
    trade_date     DATE NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('OK', 'MISSING', 'PARTIAL')),
    expected_bars  INT,
    actual_bars    INT,
    notes          TEXT,
    checked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (contract_id, trade_date)
);

CREATE INDEX idx_collection_log_status ON collection_log (trade_date, status);
