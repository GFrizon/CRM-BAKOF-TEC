import json
from datetime import date, datetime
from pathlib import Path


def _storage_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "carteiras_movimento_diario.json"


def _to_iso_data(valor):
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    return str(valor or "")


def salvar_movimento_carteira(*, carteira: str, data_ref, atualizado_em, entraram, sairam, total):
    carteira_key = str(carteira or "").strip().lower()
    if not carteira_key:
        return

    path = _storage_path()
    payload = {"carteiras": {}}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {"carteiras": {}}
        except Exception:
            payload = {"carteiras": {}}

    carteiras = payload.get("carteiras")
    if not isinstance(carteiras, dict):
        carteiras = {}
        payload["carteiras"] = carteiras

    carteira_obj = carteiras.get(carteira_key)
    if not isinstance(carteira_obj, dict):
        carteira_obj = {"dias": {}}
        carteiras[carteira_key] = carteira_obj

    dias = carteira_obj.get("dias")
    if not isinstance(dias, dict):
        dias = {}
        carteira_obj["dias"] = dias

    chave = _to_iso_data(data_ref)
    dias[chave] = {
        "data_ref": chave,
        "atualizado_em": (
            atualizado_em.isoformat()
            if isinstance(atualizado_em, datetime)
            else str(atualizado_em or "")
        ),
        "entraram": list(entraram or []),
        "sairam": list(sairam or []),
        "total": int(total or 0),
    }

    chaves_ordenadas = sorted(dias.keys(), reverse=True)
    for antiga in chaves_ordenadas[60:]:
        dias.pop(antiga, None)

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def carregar_movimentos_carteira_mes(carteira: str, ano: int, mes: int):
    carteira_key = str(carteira or "").strip().lower()
    path = _storage_path()
    if not carteira_key or not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        carteiras = payload.get("carteiras") if isinstance(payload, dict) else None
        if not isinstance(carteiras, dict):
            return []
        carteira_obj = carteiras.get(carteira_key)
        dias = carteira_obj.get("dias") if isinstance(carteira_obj, dict) else None
        if not isinstance(dias, dict):
            return []
        prefixo = f"{int(ano):04d}-{int(mes):02d}-"
        itens = []
        for chave, valor in dias.items():
            if str(chave).startswith(prefixo) and isinstance(valor, dict):
                itens.append(valor)
        itens.sort(key=lambda x: str(x.get("data_ref") or ""))
        return itens
    except Exception:
        return []
