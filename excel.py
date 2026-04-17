import re
from typing import Optional, List
import pandas as pd


def norm_name(s: str) -> str:
    """Normalize client name: strip, collapse spaces, uppercase."""
    return re.sub(r'\s+', ' ', str(s).strip()).upper()


def to_float(val) -> float:
    try:
        return float(str(val).replace(',', '').replace('$', '').strip())
    except (ValueError, TypeError):
        return 0.0


def find_header_row(df: pd.DataFrame) -> int:
    """Find the row index that contains 'Client' as a column header."""
    for i, row in df.iterrows():
        vals = [str(v).strip() for v in row.values]
        if 'Client' in vals:
            return i
    return 0


def parse_studio_export(file_bytes: bytes, filename: str) -> dict:
    """
    Parse a Studio ERP Excel export.

    Studio format:
      - Rows 0-3: title block (studio name, date, blanks)
      - Row 4:    real headers (Client, Room, Purchase, Selling, ...)
      - Data:     one row per room per client + subtotal row (Room='')
                  + blank separator row between clients
      - TIME BILLING appears as a Room value
    """
    xl = pd.ExcelFile(file_bytes)
    sheet_name = next(
        (s for s in xl.sheet_names if 'PROFIT' in s.upper()),
        xl.sheet_names[0]
    )

    # First pass — find real header row
    raw = xl.parse(sheet_name, dtype=str, header=None)
    raw = raw.fillna('')
    header_idx = find_header_row(raw)

    # Second pass — parse with correct header
    df = xl.parse(sheet_name, dtype=str, skiprows=header_idx, header=0)
    df = df.fillna('')
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].str.strip()

    row_count = len(df)

    if 'Client' not in df.columns:
        raise ValueError(
            f"Could not find Client column. Found: {list(df.columns)}"
        )

    selling_col = next((c for c in df.columns if c.strip() == 'Selling'), None)
    profit_col  = next((c for c in df.columns if c.strip() == 'Total Profit'), None)
    room_col    = 'Room' if 'Room' in df.columns else None

    # Subtotal rows: Client set, Room blank
    if room_col:
        subtotals = df[(df['Client'] != '') & (df[room_col] == '')]
        tb_rows   = df[df[room_col].str.upper() == 'TIME BILLING']
    else:
        subtotals = df[df['Client'] != '']
        tb_rows   = pd.DataFrame()

    # Time billing per client
    tb_map: dict = {}
    if not tb_rows.empty and profit_col:
        for _, row in tb_rows.iterrows():
            c = row['Client'].strip()
            if c:
                tb_map[c] = tb_map.get(c, 0.0) + to_float(row.get(profit_col, 0))

    # Build client list
    clients = []
    for _, row in subtotals.iterrows():
        name = row['Client'].strip()
        if not name:
            continue
        revenue = to_float(row.get(selling_col, 0)) if selling_col else 0.0
        profit  = to_float(row.get(profit_col,  0)) if profit_col  else 0.0
        tb      = tb_map.get(name, 0.0)
        margin  = round((profit / revenue * 100), 1) if revenue else 0.0
        clients.append({
            'name':            name,
            'name_normalized': norm_name(name),
            'revenue':         revenue,
            'profit':          profit,
            'time_billing':    tb,
            'margin':          margin,
        })

    clients.sort(key=lambda c: c['name'])

    return {
        'row_count': row_count,
        'sheet':     sheet_name,
        'clients':   clients,
    }
