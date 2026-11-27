#!/usr/bin/env python3
"""
Parses Fyers symbol master CSVs and populates instruments + contracts for
Nifty options (NSE), Sensex options (BSE), Crude Oil options (MCX).

Source files (headerless CSV, 21 columns, downloaded from
https://public.fyers.in/sym_details/<FILE>.csv):
  NSE_FO.csv  -> NIFTY options  -> exchange 'NSE'
  BSE_FO.csv  -> SENSEX options -> exchange 'BSE'
  MCX_COM.csv -> CRUDEOIL options -> exchange 'MCX'

Relevant columns (0-indexed), confirmed by manual inspection of raw rows:
  0  fyToken
  3  lot_size
  4  tick_size
  8  expiry (unix epoch seconds)
  9  fySymbol, e.g. 'NSE:NIFTY2690818850CE' / 'MCX:CRUDEOIL26SEP7000CE'
  12 exchange scrip/token (broker-style numeric token, distinct from fyToken)
  13 underlying symbol, e.g. 'NIFTY', 'SENSEX', 'CRUDEOIL'
  15 strike price (-1.0 for futures rows)
  16 option type: 'CE' / 'PE' / 'XX' (futures) -- only CE/PE are ingested

Only CE/PE rows for the target underlying are kept; futures (XX, strike -1.0)
are skipped entirely.
"""
import csv
import datetime
import subprocess
import sys
import urllib.request

SOURCES = [
    # (filename, underlying symbol in col13, exchange, segment)
    ("NSE_FO.csv", "NIFTY", "NSE", "INDEX"),
    ("BSE_FO.csv", "SENSEX", "BSE", "INDEX"),
    ("MCX_COM.csv", "CRUDEOIL", "MCX", "COMMODITY"),
]

BASE_URL = "https://public.fyers.in/sym_details/{}"


def download(filename):
    path = f"/tmp/{filename}"
    urllib.request.urlretrieve(BASE_URL.format(filename), path)
    return path


def parse_file(path, underlying, exchange, segment):
    """Yields dicts for CE/PE rows matching `underlying`."""
    rows = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        for cols in reader:
            if len(cols) < 21:
                continue
            if cols[13] != underlying:
                continue
            opt_type = cols[16]
            if opt_type not in ("CE", "PE"):
                continue  # skip futures (XX) and anything unexpected

            fy_symbol = cols[9]
            trading_symbol = fy_symbol.split(":", 1)[1] if ":" in fy_symbol else fy_symbol
            expiry_date = datetime.date.fromtimestamp(int(cols[8]))

            rows.append({
                "underlying": underlying,
                "exchange": exchange,
                "segment": segment,
                "trading_symbol": trading_symbol,
                "exchange_token": cols[12],
                "expiry_date": expiry_date,
                "strike_price": float(cols[15]),
                "option_type": opt_type,
                "lot_size": int(cols[3]),
                "tick_size": float(cols[4]),
            })
    return rows


def to_sql_value(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, datetime.date):
        return f"'{v.isoformat()}'"
    escaped = str(v).replace("'", "''")
    return f"'{escaped}'"


def build_sql(all_rows):
    """all_rows: list of parsed contract dicts across all sources."""
    lines = ["BEGIN;"]

    # instruments: one row per (underlying, exchange), lot_size/tick_size
    # taken from the first contract row seen for that underlying.
    instruments = {}
    for r in all_rows:
        key = (r["underlying"], r["exchange"])
        if key not in instruments:
            instruments[key] = r

    for (underlying, exchange), r in instruments.items():
        lines.append(
            "INSERT INTO instruments (symbol, exchange, segment, lot_size, tick_size) VALUES "
            f"({to_sql_value(underlying)}, {to_sql_value(exchange)}, {to_sql_value(r['segment'])}, "
            f"{to_sql_value(r['lot_size'])}, {to_sql_value(r['tick_size'])}) "
            "ON CONFLICT (symbol) DO UPDATE SET "
            "lot_size = EXCLUDED.lot_size, tick_size = EXCLUDED.tick_size;"
        )

    # contracts: instrument_id resolved by subquery on symbol, since
    # BIGSERIAL ids aren't known until after the instruments insert above.
    batch = []
    for r in all_rows:
        values = (
            f"((SELECT instrument_id FROM instruments WHERE symbol = {to_sql_value(r['underlying'])}), "
            f"{to_sql_value(r['trading_symbol'])}, {to_sql_value(r['exchange_token'])}, "
            f"{to_sql_value(r['expiry_date'])}, {to_sql_value(r['strike_price'])}, "
            f"{to_sql_value(r['option_type'])}, {to_sql_value(r['lot_size'])}, "
            f"NULL, {to_sql_value(r['expiry_date'])})"
        )
        batch.append(values)

    cols = (
        "instrument_id, trading_symbol, exchange_token, expiry_date, "
        "strike_price, option_type, lot_size, first_traded_date, last_traded_date"
    )
    for i in range(0, len(batch), 200):
        chunk = batch[i:i + 200]
        lines.append(
            f"INSERT INTO contracts ({cols}) VALUES\n"
            + ",\n".join(chunk)
            + "\nON CONFLICT (trading_symbol) DO UPDATE SET "
              "exchange_token = EXCLUDED.exchange_token, "
              "last_traded_date = EXCLUDED.last_traded_date;"
        )

    lines.append("COMMIT;")
    return "\n".join(lines)


def run_sql(sql_path):
    result = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "fno_archive", "-f", sql_path],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise SystemExit(result.returncode)


def main():
    sample_only = "--sample" in sys.argv
    apply_db = "--apply" in sys.argv

    all_rows = []
    for filename, underlying, exchange, segment in SOURCES:
        path = download(filename)
        rows = parse_file(path, underlying, exchange, segment)
        print(f"{filename}: {len(rows)} {underlying} CE/PE contracts parsed (exchange={exchange})")
        if sample_only:
            for r in rows[:5]:
                print(f"  {r}")
        all_rows.extend(rows)

    print(f"\nTotal contracts parsed: {len(all_rows)}")

    if sample_only:
        return

    sql = build_sql(all_rows)
    sql_path = "/tmp/instruments_contracts_data.sql"
    with open(sql_path, "w") as f:
        f.write(sql)
    print(f"Wrote SQL to {sql_path}")

    if apply_db:
        run_sql(sql_path)


if __name__ == "__main__":
    main()
