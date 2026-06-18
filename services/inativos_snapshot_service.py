from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from services.daily_snapshot_history import (
    carregar_primeiro_snapshot_do_mes,
    carregar_snapshot_do_historico,
    carregar_snapshot_na_data_ou_anterior,
    listar_datas_snapshot,
    salvar_snapshot_em_historico,
)


def _snapshot_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "inativos_oracle_snapshot.json"


def _data_ref_str(data_ref: date | None = None) -> str:
    return (data_ref or datetime.now().date()).isoformat()


def _coagir_dt_iso(valor: Any) -> str | None:
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day).isoformat()
    return str(valor)


def salvar_snapshot_inativos_oracle(rows: list[dict[str, Any]], data_ref: date | None = None) -> dict[str, Any]:
    data_ref_txt = _data_ref_str(data_ref)
    itens = []
    codigos = []
    vistos = set()
    for row in rows or []:
        cd = str((row or {}).get("cd_cliente") or "").strip()
        if not cd or cd in vistos:
            continue
        vistos.add(cd)
        codigos.append(cd)
        itens.append(
            {
                "cd_cliente": cd,
                "cd_centralizado": (row or {}).get("cd_centralizado"),
                "nome_centralizadora": (row or {}).get("nome_centralizadora"),
                "dt_pedido": _coagir_dt_iso((row or {}).get("dt_pedido")),
            }
        )

    payload = {
        "data_ref": data_ref_txt,
        "atualizado_em": datetime.now().isoformat(),
        "janela_min_dias": 181,
        "janela_max_dias": 1095,
        "total": len(codigos),
        "codigos": codigos,
        "itens": itens,
    }
    path = _snapshot_path()
    return salvar_snapshot_em_historico(
        path,
        payload,
        data_ref=data_ref,
        dias_detalhados=31,
        meses_historicos=24,
    )


def carregar_snapshot_inativos_oracle(data_ref: date | None = None) -> dict[str, Any] | None:
    payload = carregar_snapshot_do_historico(_snapshot_path(), data_ref=data_ref)
    if not isinstance((payload or {}).get("itens"), list):
        return None
    return payload


def carregar_snapshot_inativos_oracle_na_data_ou_anterior(data_ref: date | None = None) -> dict[str, Any] | None:
    payload = carregar_snapshot_na_data_ou_anterior(_snapshot_path(), data_ref=data_ref)
    if not isinstance((payload or {}).get("itens"), list):
        return None
    return payload


def carregar_primeiro_snapshot_inativos_oracle_mes(ano: int, mes: int) -> dict[str, Any] | None:
    payload = carregar_primeiro_snapshot_do_mes(_snapshot_path(), ano, mes)
    if not isinstance((payload or {}).get("itens"), list):
        return None
    return payload


def listar_datas_snapshot_inativos_oracle() -> list[str]:
    return listar_datas_snapshot(_snapshot_path())


def snapshot_inativos_cobre_janela(
    payload: dict[str, Any] | None,
    *,
    dias_min: int = 181,
    dias_max: int = 1095,
) -> bool:
    if not payload:
        return False
    try:
        return (
            int((payload or {}).get("janela_min_dias") or 0) == int(dias_min)
            and int((payload or {}).get("janela_max_dias") or 0) == int(dias_max)
        )
    except Exception:
        return False


def montar_mapa_snapshot_inativos(payload: dict[str, Any] | None) -> tuple[set[str], dict[str, dict[str, Any]]]:
    codigos = set()
    centralizadora_por_cd = {}
    for item in ((payload or {}).get("itens") or []):
        cd = str((item or {}).get("cd_cliente") or "").strip()
        if not cd:
            continue
        codigos.add(cd)
        centralizadora_por_cd[cd] = {
            "cd_centralizado": (item or {}).get("cd_centralizado"),
            "nome_centralizadora": (item or {}).get("nome_centralizadora"),
        }
    return codigos, centralizadora_por_cd
