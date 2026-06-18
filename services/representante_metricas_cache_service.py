from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def _cache_path(nome: str) -> Path:
    return _data_dir() / nome


def _hoje_iso() -> str:
    return datetime.now().date().isoformat()


def invalidar_metricas_representante_diarias() -> None:
    caminhos = [_cache_path("representante_pagamento_medio_cache.json")]
    caminhos.extend(_data_dir().glob("representante_meses_compra_*.json"))
    for path in caminhos:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _carregar_payload(path: Path) -> dict:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _salvar_payload(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def carregar_pagamento_medio_representante(codigos: list[str]) -> dict[str, int]:
    codigos_norm = sorted({str(c or "").strip() for c in (codigos or []) if str(c or "").strip()})
    if not codigos_norm:
        return {}

    path = _cache_path("representante_pagamento_medio_cache.json")
    payload = _carregar_payload(path)
    valores = payload.get("valores") if payload.get("data_ref") == _hoje_iso() else {}
    if not isinstance(valores, dict):
        valores = {}

    faltantes = [codigo for codigo in codigos_norm if codigo not in valores]
    if faltantes:
        try:
            from oracle_service import get_dias_media_recebimento_oracle

            novos = get_dias_media_recebimento_oracle(faltantes) or {}
            for codigo in faltantes:
                valores[codigo] = novos.get(codigo)
            _salvar_payload(
                path,
                {
                    "data_ref": _hoje_iso(),
                    "atualizado_em": datetime.now().isoformat(),
                    "valores": valores,
                },
            )
        except Exception:
            pass

    return {
        codigo: valores.get(codigo)
        for codigo in codigos_norm
    }


def carregar_meses_compra_representante(
    codigos: list[str],
    *,
    periodo: str = "ano_atual",
) -> dict[str, list[str]]:
    codigos_norm = sorted({str(c or "").strip() for c in (codigos or []) if str(c or "").strip()})
    if not codigos_norm:
        return {}

    periodo_norm = str(periodo or "ano_atual").strip().lower() or "ano_atual"
    path = _cache_path(f"representante_meses_compra_{periodo_norm}.json")
    payload = _carregar_payload(path)
    valores = payload.get("valores") if payload.get("data_ref") == _hoje_iso() else {}
    if not isinstance(valores, dict):
        valores = {}

    faltantes = [codigo for codigo in codigos_norm if codigo not in valores]
    if faltantes:
        try:
            from oracle_service import get_meses_compra_por_cliente_oracle

            novos = get_meses_compra_por_cliente_oracle(faltantes, periodo=periodo_norm) or {}
            for codigo in faltantes:
                meses = novos.get(codigo) or []
                valores[codigo] = list(meses) if isinstance(meses, (list, tuple, set)) else []
            _salvar_payload(
                path,
                {
                    "data_ref": _hoje_iso(),
                    "atualizado_em": datetime.now().isoformat(),
                    "periodo": periodo_norm,
                    "valores": valores,
                },
            )
        except Exception:
            pass

    resultado = {}
    for codigo in codigos_norm:
        bruto = valores.get(codigo) or []
        meses = []
        if isinstance(bruto, (list, tuple, set)):
            for item in bruto:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    try:
                        meses.append((int(item[0]), int(item[1])))
                    except (ValueError, TypeError):
                        pass
        resultado[codigo] = meses
    return resultado
