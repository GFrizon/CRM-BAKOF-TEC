import re

import pandas as pd


def s(v):
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip()


def so_digits(v):
    return re.sub(r"\D+", "", s(v))


def formatar_dinheiro(valor):
    try:
        v = float(valor or 0)
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def formatar_dinheiro_filter(valor):
    try:
        v = float(valor or 0)
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return "R$ 0,00"


def get_pos(row, idx):
    try:
        return row.iloc[idx]
    except Exception:
        return ""


def _kfmt(n):
    try:
        n = float(n or 0)
    except Exception:
        n = 0
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(int(n))


def _percent(a, b):
    try:
        a = float(a or 0)
        b = float(b or 0)
        return (a / b * 100) if b else 0.0
    except Exception:
        return 0.0
