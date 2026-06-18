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
    return Path(__file__).resolve().parents[1] / "data" / "ativos_oracle_snapshot.json"


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


def salvar_snapshot_ativos_oracle(rows: list[dict[str, Any]], data_ref: date | None = None) -> dict[str, Any]:
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
                "cliente": (row or {}).get("cliente"),
                "cnpj": (row or {}).get("cnpj"),
                "telefone1": (row or {}).get("telefone1"),
                "telefone2": (row or {}).get("telefone2"),
                "municipio": (row or {}).get("municipio"),
                "uf": (row or {}).get("uf"),
                "contato": (row or {}).get("contato"),
                "conceito": (row or {}).get("conceito"),
                "consultor": (row or {}).get("consultor"),
                "representante": (row or {}).get("representante"),
                "cd_centralizado": (row or {}).get("cd_centralizado"),
                "nome_centralizadora": (row or {}).get("nome_centralizadora"),
                "dt_pedido": _coagir_dt_iso((row or {}).get("dt_pedido")),
                "total_pedido": (row or {}).get("total_pedido"),
                "situacao": (row or {}).get("situacao"),
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


def carregar_snapshot_ativos_oracle(data_ref: date | None = None) -> dict[str, Any] | None:
    payload = carregar_snapshot_do_historico(_snapshot_path(), data_ref=data_ref)
    if not isinstance((payload or {}).get("itens"), list):
        return None
    return payload


def carregar_snapshot_ativos_oracle_na_data_ou_anterior(data_ref: date | None = None) -> dict[str, Any] | None:
    payload = carregar_snapshot_na_data_ou_anterior(_snapshot_path(), data_ref=data_ref)
    if not isinstance((payload or {}).get("itens"), list):
        return None
    return payload


def carregar_primeiro_snapshot_ativos_oracle_mes(ano: int, mes: int) -> dict[str, Any] | None:
    payload = carregar_primeiro_snapshot_do_mes(_snapshot_path(), ano, mes)
    if not isinstance((payload or {}).get("itens"), list):
        return None
    return payload


def listar_datas_snapshot_ativos_oracle() -> list[str]:
    return listar_datas_snapshot(_snapshot_path())


def montar_mapa_snapshot_ativos(payload: dict[str, Any] | None) -> tuple[set[str], dict[str, dict[str, Any]]]:
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


def rows_snapshot_ativos(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = []
    for item in ((payload or {}).get("itens") or []):
        row = dict(item or {})
        row["dt_pedido"] = _restaurar_dt(row.get("dt_pedido"))
        rows.append(row)
    return rows
