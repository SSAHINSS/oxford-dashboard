import re
from typing import Optional, List
import pandas as pd


def norm_name(s: str) -> str:
    """Normalize client name: strip, collapse spaces, uppercase."""
    return re.sub(r'\s+', ' ', str(s).strip()).upper()


def detect_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Find a column by trying candidate names (case-insensitive, then partial)."""
    cols_lower = {c.strip().lower(): c for c in df.columns}
    for candidate in candidates:
        if candidate.lower() in cols_lower:
            return cols_lower[candidate.lower()]
    for candidate in candidates:
        for key, col in cols_lower.items():
            if candidate.lower() in key:
                return col
    return None


def parse_studio_export(file_bytes: bytes, filename: str) -> dict:
    """
    Parse a Studio ERP Excel export.
    Returns:
      {
        row_count: int,
        sheet: str,
        clients: [{ name, name_normalized, revenue, profit, time_billing, margin }]
      }
    """
    xl = pd.ExcelFile(file_bytes)
    sheet_name = next(
        (s for s in xl.sheet_names if 'PROFIT' in s.upper()),
        xl.sheet_names[0]
    )
    df = xl.parse(sheet_name, dtype=str)
    df = df.fillna('')

    row_count = len(df)

    client_col  = detect_col(df, ['Client', 'client', 'PROJECT', 'project', 'CLIENT'])
    selling_col = detect_col(df, ['Selling', 'selling', 'Revenue', 'revenue', 'SELLING'])
    profit_col  = detect_col(df, ['Total Profit', 'TotalProfit', 'total profit', 'Total_Profit', 'Profit'])
    type_col    = detect_col(df, ['Type', 'type', 'TYPE'])

    if not client_col:
        raise ValueError(
            "Could not find a Client column. "
            "Columns found: " + ", ".join(str(c) for c in df.columns)
        )

    def to_float(val) -> float:
        try:
            return float(str(val).replace(',', '').replace('$', '').strip())
        except (ValueError, TypeError):
            return 0.0

    client_map = {}
    for _, row in df.iterrows():
        client_raw = str(row.get(client_col, '')).strip()
        if not client_raw:
            continue
        norm = norm_name(client_raw)
        if norm not in client_map:
            client_map[norm] = {
                'name': client_raw,
                'name_normalized': norm,
                'revenue': 0.0,
                'profit': 0.0,
                'time_billing': 0.0,
            }

        selling  = to_float(row.get(selling_col, 0)) if selling_col else 0.0
        profit   = to_float(row.get(profit_col, 0))  if profit_col  else 0.0
        row_type = str(row.get(type_col, '')).strip().upper() if type_col else ''

        client_map[norm]['revenue'] += selling
        client_map[norm]['profit']  += profit
        if row_type == 'TIME BILLING':
            client_map[norm]['time_billing'] += profit

    clients = []
    for data in client_map.values():
        rev  = data['revenue']
        prof = data['profit']
        data['margin'] = round((prof / rev * 100), 1) if rev else 0.0
        clients.append(data)

    clients.sort(key=lambda c: c['name'])

    return {
        'row_count': row_count,
        'sheet':     sheet_name,
        'clients':   clients,
    }
