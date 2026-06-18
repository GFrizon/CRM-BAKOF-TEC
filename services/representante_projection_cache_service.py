from __future__ import annotations

import json
import re
from decimal import Decimal
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = 1


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def _hoje_iso() -> str:
    return datetime.now().date().isoformat()


def invalidar_projecoes_representante_diarias() -> None:
    for path in _data_dir().glob("representante_proj_*.json"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _slug(valor: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(valor or "").strip())
    return base.strip("_") or "vazio"


def _cache_path(
    *,
    codigo_representante: str,
    carteira: str,
    agrupar_por: str,
    periodo_recencia: str,
) -> Path:
    nome = (
        "representante_proj_"
        f"{_slug(codigo_representante)}_"
        f"{_slug(carteira)}_"
        f"{_slug(agrupar_por)}_"
        f"{_slug(periodo_recencia)}.json"
    )
    return _data_dir() / nome


def _serializar(valor: Any) -> Any:
    if isinstance(valor, datetime):
        return {"__tipo__": "datetime", "valor": valor.isoformat()}
    if isinstance(valor, date):
        return {"__tipo__": "date", "valor": valor.isoformat()}
    if isinstance(valor, Decimal):
        try:
            return int(valor) if valor == valor.to_integral_value() else float(valor)
        except Exception:
            return float(valor)
    if isinstance(valor, dict):
        return {str(chave): _serializar(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_serializar(item) for item in valor]
    if isinstance(valor, set):
        return [_serializar(item) for item in sorted(valor, key=lambda item: str(item))]
    return valor


def _desserializar(valor: Any) -> Any:
    if isinstance(valor, list):
        return [_desserializar(item) for item in valor]
    if not isinstance(valor, dict):
        return valor
    if valor.get("__tipo__") == "datetime":
        try:
            return datetime.fromisoformat(str(valor.get("valor") or ""))
        except Exception:
            return None
    if valor.get("__tipo__") == "date":
        try:
            return date.fromisoformat(str(valor.get("valor") or ""))
        except Exception:
            return None
    return {chave: _desserializar(item) for chave, item in valor.items()}


def _carregar_payload(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _salvar_payload(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def carregar_ou_gerar_projecao_representante(
    *,
    codigo_representante: str,
    carteira: str,
    agrupar_por: str,
    periodo_recencia: str,
    gerador: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    path = _cache_path(
        codigo_representante=codigo_representante,
        carteira=carteira,
        agrupar_por=agrupar_por,
        periodo_recencia=periodo_recencia,
    )
    payload = _carregar_payload(path)
    if (
        payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("data_ref") == _hoje_iso()
        and isinstance(payload.get("projecao"), dict)
    ):
        return _desserializar(payload["projecao"])

    projecao = gerador() or {}
    _salvar_payload(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "data_ref": _hoje_iso(),
            "atualizado_em": datetime.now().isoformat(),
            "projecao": _serializar(projecao),
        },
    )
    return projecao
