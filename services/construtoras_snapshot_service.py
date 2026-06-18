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
    return Path(__file__).resolve().parents[1] / "data" / "construtoras_oracle_snapshot.json"


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


def _restaurar_dt(valor: Any) -> Any:
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor
    try:
        return datetime.fromisoformat(str(valor))
    except Exception:
        return valor


def salvar_snapshot_construtoras_oracle(rows: list[dict[str, Any]], data_ref: date | None = None) -> dict[str, Any]:
    data_ref_txt = _data_ref_str(data_ref)
    itens = []
    codigos = []
    vistos = set()
    for row in rows or []:
        row = row or {}
        cd = str(row.get("cd_cliente") or "").strip()
        if not cd or cd in vistos:
            continue
        vistos.add(cd)
        codigos.append(cd)
        itens.append(
            {
                "dt_pedido": _coagir_dt_iso(row.get("dt_pedido")),
                "cd_cliente": cd,
                "cliente": row.get("cliente"),
                "cnpj": row.get("cnpj"),
                "telefone1": row.get("telefone1"),
                "telefone2": row.get("telefone2"),
                "municipio": row.get("municipio"),
                "uf": row.get("uf"),
                "contato": row.get("contato"),
                "cd_centralizado": row.get("cd_centralizado"),
                "nome_centralizadora": row.get("nome_centralizadora"),
                "representante": row.get("representante"),
                "total_pedido": row.get("total_pedido"),
                "situacao": row.get("situacao"),
                "desc_cond_pagto": row.get("desc_cond_pagto"),
                "cd_unid_de_neg": row.get("cd_unid_de_neg"),
                "consultor": row.get("consultor"),
                "conceito": row.get("conceito"),
            }
        )

    payload = {
        "data_ref": data_ref_txt,
        "atualizado_em": datetime.now().isoformat(),
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


def carregar_snapshot_construtoras_oracle(data_ref: date | None = None) -> dict[str, Any] | None:
    payload = carregar_snapshot_do_historico(_snapshot_path(), data_ref=data_ref)
    if not isinstance((payload or {}).get("itens"), list):
        return None
    return payload


def carregar_snapshot_construtoras_oracle_na_data_ou_anterior(data_ref: date | None = None) -> dict[str, Any] | None:
    payload = carregar_snapshot_na_data_ou_anterior(_snapshot_path(), data_ref=data_ref)
    if not isinstance((payload or {}).get("itens"), list):
        return None
    return payload


def carregar_primeiro_snapshot_construtoras_oracle_mes(ano: int, mes: int) -> dict[str, Any] | None:
    payload = carregar_primeiro_snapshot_do_mes(_snapshot_path(), ano, mes)
    if not isinstance((payload or {}).get("itens"), list):
        return None
    return payload


def listar_datas_snapshot_construtoras_oracle() -> list[str]:
    return listar_datas_snapshot(_snapshot_path())


def rows_snapshot_construtoras(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = []
    for item in ((payload or {}).get("itens") or []):
        row = dict(item or {})
        row["dt_pedido"] = _restaurar_dt(row.get("dt_pedido"))
        rows.append(row)
    return rows
