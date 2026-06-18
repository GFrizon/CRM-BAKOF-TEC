import json
from datetime import date, datetime
from pathlib import Path


def _storage_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "inativos_movimento_diario.json"


def _to_iso_data(valor):
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    return str(valor or "")


def salvar_movimento_inativos(
    *,
    data_ref,
    atualizado_em,
    entraram,
    sairam,
    total_inativos,
):
    path = _storage_path()
    payload = {"dias": {}}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {"dias": {}}
        except Exception:
            payload = {"dias": {}}

    dias = payload.get("dias")
    if not isinstance(dias, dict):
        dias = {}
        payload["dias"] = dias

    chave = _to_iso_data(data_ref)
    dias[chave] = {
        "data_ref": chave,
        "atualizado_em": (atualizado_em.isoformat() if isinstance(atualizado_em, datetime) else str(atualizado_em or "")),
        "entraram": list(entraram or []),
        "sairam": list(sairam or []),
        "total_inativos": int(total_inativos or 0),
    }

    # Mantem historico enxuto (ultimos 45 dias)
    chaves_ordenadas = sorted(dias.keys(), reverse=True)
    for antiga in chaves_ordenadas[45:]:
        dias.pop(antiga, None)

    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def carregar_movimento_inativos(data_ref=None):
    path = _storage_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        dias = payload.get("dias") if isinstance(payload, dict) else None
        if not isinstance(dias, dict) or not dias:
            return None
        if data_ref is not None:
            chave = _to_iso_data(data_ref)
            return dias.get(chave)
        chave_recente = sorted(dias.keys(), reverse=True)[0]
        return dias.get(chave_recente)
    except Exception:
        return None


def carregar_movimentos_inativos_mes(ano: int, mes: int):
    path = _storage_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        dias = payload.get("dias") if isinstance(payload, dict) else None
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
