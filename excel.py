import re
import io
from typing import Optional
from datetime import datetime
from collections import defaultdict
import pandas as pd


def norm_name(s: str) -> str:
    return re.sub(r'\s+', ' ', str(s).strip()).upper()


def to_float(val) -> float:
    try:
        return float(str(val).replace(',', '').replace('$', '').strip())
    except (ValueError, TypeError):
        return 0.0


def parse_date(val) -> Optional[datetime]:
    val = str(val).strip()
    if not val:
        return None
    for fmt in ['%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y']:
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def find_header_row(df: pd.DataFrame) -> int:
    for i, row in df.iterrows():
        if 'Client' in [str(v).strip() for v in row.values]:
            return i
    return 0


EXCLUDE = {'REPORT TOTAL:', 'REPORT TOTAL', 'CLIENT OFFICE EXPENSE',
           'GRAND TOTAL', 'TOTAL'}


def parse_studio_export(file_bytes: bytes, filename: str) -> dict:
    xl = pd.ExcelFile(io.BytesIO(file_bytes) if isinstance(file_bytes, bytes) else file_bytes)
    sheet_name = next((s for s in xl.sheet_names if 'PROFIT' in s.upper()), xl.sheet_names[0])

    raw = xl.parse(sheet_name, dtype=str, header=None)
    raw = raw.fillna('')
    header_idx = find_header_row(raw)

    df = xl.parse(sheet_name, dtype=str, skiprows=header_idx, header=0)
    df = df.fillna('')
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].str.strip()

    row_count = len(df)

    if 'Client' not in df.columns:
        raise ValueError(f"Could not find Client column. Found: {list(df.columns)}")

    selling_col = next((c for c in df.columns if c.strip() == 'Selling'), None)
    profit_col  = next((c for c in df.columns if c.strip() == 'Total Profit'), None)
    room_col    = 'Room'         if 'Room'         in df.columns else None
    item_col    = 'Item'         if 'Item'         in df.columns else None
    date_col    = 'Invoice Date' if 'Invoice Date' in df.columns else None

    has_items = item_col is not None and date_col is not None

    # ── Build set of real client names from item rows (fast O(n) lookup) ──
    real_client_norms: set = set()
    if has_items:
        item_mask = df[item_col] != ''
        for name in df.loc[item_mask, 'Client'].unique():
            name = str(name).strip()
            if name:
                real_client_norms.add(norm_name(name))

    # ── Monthly aggregates from item rows ──
    monthly: dict = defaultdict(lambda: defaultdict(
        lambda: {'revenue': 0.0, 'profit': 0.0, 'time_billing': 0.0}
    ))
    date_min_map: dict = {}
    date_max_map: dict = {}

    if has_items:
        for _, row in df[df[item_col] != ''].iterrows():
            client = str(row['Client']).strip()
            if not client:
                continue
            d = parse_date(row.get(date_col, ''))
            if not d:
                continue
            key = f"{d.year}-{d.month:02d}"
            is_tb = room_col and str(row.get(room_col, '')).upper() == 'TIME BILLING'
            selling = to_float(row.get(selling_col, 0)) if selling_col else 0.0
            profit  = to_float(row.get(profit_col,  0)) if profit_col  else 0.0
            monthly[client][key]['revenue'] += selling
            monthly[client][key]['profit']  += profit
            if is_tb:
                monthly[client][key]['time_billing'] += profit
            # Date range
            prev_min = date_min_map.get(client)
            prev_max = date_max_map.get(client)
            date_min_map[client] = d if prev_min is None else min(prev_min, d)
            date_max_map[client] = d if prev_max is None else max(prev_max, d)

    # ── Client subtotal rows ──
    if room_col:
        mask = (df['Client'] != '') & (df[room_col] == '')
        if item_col:
            mask = mask & (df[item_col] == '')
        subtotals = df[mask]
        tb_rows = df[df[room_col].str.upper() == 'TIME BILLING'] if not has_items else pd.DataFrame()
    else:
        subtotals = df[df['Client'] != '']
        tb_rows   = pd.DataFrame()

    # Time billing for old format
    tb_map: dict = {}
    if not tb_rows.empty and profit_col:
        for _, row in tb_rows.iterrows():
            c = str(row['Client']).strip()
            if c:
                tb_map[c] = tb_map.get(c, 0.0) + to_float(row.get(profit_col, 0))

    # ── Build final client list ──
    seen_norms: set = set()
    clients = []

    for _, row in subtotals.iterrows():
        name = str(row['Client']).strip()
        if not name:
            continue
        norm = norm_name(name)

        # Skip summary rows
        if norm in EXCLUDE or norm.startswith('REPORT'):
            continue

        # In new format, skip room-subtotal rows
        # (e.g. "ACCARDI - 100 - ENTRY HALL" — not a real client)
        if has_items and norm not in real_client_norms:
            continue

        # Deduplicate
        if norm in seen_norms:
            continue
        seen_norms.add(norm)

        revenue = to_float(row.get(selling_col, 0)) if selling_col else 0.0
        profit  = to_float(row.get(profit_col,  0)) if profit_col  else 0.0
        tb      = tb_map.get(name, 0.0)
        margin  = round((profit / revenue * 100), 1) if revenue else 0.0

        mn = date_min_map.get(name)
        mx = date_max_map.get(name)

        clients.append({
            'name':            name,
            'name_normalized': norm,
            'revenue':         revenue,
            'profit':          profit,
            'time_billing':    tb,
            'margin':          margin,
            'monthly_data':    dict(monthly.get(name, {})),
            'date_min':        mn.strftime('%Y-%m-%d') if mn else None,
            'date_max':        mx.strftime('%Y-%m-%d') if mx else None,
        })

    clients.sort(key=lambda c: c['name'])

    return {
        'row_count': row_count,
        'sheet':     sheet_name,
        'clients':   clients,
    }
