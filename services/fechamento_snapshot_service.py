import json
from datetime import datetime
from pathlib import Path


def _storage_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "fechamento_mensal_snapshot.json"


def _carregar_payload():
    path = _storage_path()
    if not path.exists():
        return {"snapshots": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {"snapshots": {}}


def _salvar_payload(payload):
    path = _storage_path()
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _chave_snapshot(tipo_operador: str, ano: int, mes: int) -> str:
    return f"{str(tipo_operador or '').strip().lower()}:{int(ano):04d}-{int(mes):02d}"


def carregar_snapshot_fechamento(tipo_operador: str, ano: int, mes: int):
    payload = _carregar_payload()
    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, dict):
        return None
    return snapshots.get(_chave_snapshot(tipo_operador, ano, mes))


def salvar_snapshot_fechamento(tipo_operador: str, ano: int, mes: int, snapshot: dict, max_versoes: int = 6):
    payload = _carregar_payload()
    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, dict):
        snapshots = {}
        payload["snapshots"] = snapshots

    chave = _chave_snapshot(tipo_operador, ano, mes)
    atual = snapshots.get(chave)
    if not isinstance(atual, dict):
        atual = {
            "tipo_operador": str(tipo_operador or "").strip().lower(),
            "ano": int(ano),
            "mes": int(mes),
            "versoes": [],
        }

    versoes = atual.get("versoes")
    if not isinstance(versoes, list):
        versoes = []
    versoes.append(dict(snapshot or {}))
    atual["versoes"] = versoes[-max_versoes:]
    atual["ultima_atualizacao"] = datetime.now().isoformat()
    atual["ultima_assinatura"] = str((snapshot or {}).get("assinatura") or "")
    atual["ultimo_snapshot"] = dict(snapshot or {})
    snapshots[chave] = atual

    if len(snapshots) > 48:
        chaves = sorted(snapshots.keys(), reverse=True)
        snapshots_filtrados = {k: snapshots[k] for k in chaves[:48]}
        payload["snapshots"] = snapshots_filtrados

    _salvar_payload(payload)
    return snapshots.get(chave)
