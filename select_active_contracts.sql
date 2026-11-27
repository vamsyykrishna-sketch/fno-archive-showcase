-- Active near-term contracts for the daily collector.
-- Scope: for each of NIFTY, SENSEX, CRUDEOIL, take only the nearest
-- unexpired expiry per underlying (all strikes, both CE/PE) -- not the
-- full 8,196-row universe. Liquidity/OI filtering deliberately deferred
-- (thresholds unset) per instruction; this is everything at that expiry.
WITH near_term AS (
    SELECT c.instrument_id, MIN(c.expiry_date) AS nearest_expiry
    FROM contracts c
    JOIN instruments i ON i.instrument_id = c.instrument_id
    WHERE i.symbol IN ('NIFTY', 'SENSEX', 'CRUDEOIL')
      AND c.expiry_date >= CURRENT_DATE
    GROUP BY c.instrument_id
)
SELECT
    c.contract_id,
    c.trading_symbol,
    c.exchange_token,
    i.exchange,
    i.symbol        AS underlying,
    c.expiry_date,
    c.strike_price,
    c.option_type,
    c.lot_size
FROM contracts c
JOIN instruments i ON i.instrument_id = c.instrument_id
JOIN near_term nt
  ON nt.instrument_id = c.instrument_id
 AND nt.nearest_expiry = c.expiry_date
ORDER BY i.symbol, c.strike_price, c.option_type;
