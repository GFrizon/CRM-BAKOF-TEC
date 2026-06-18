from __future__ import annotations

from datetime import date
from pathlib import Path

from services.daily_snapshot_history import (
    carregar_snapshot_do_historico,
    carregar_snapshot_na_data_ou_anterior,
    listar_datas_snapshot,
    salvar_snapshot_em_historico,
)


def _snapshot_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "cranio_ai_summary_snapshot.json"


def salvar_snapshot_cranio_ai_summary(payload: dict, data_ref: date | None = None):
    return salvar_snapshot_em_historico(_snapshot_path(), payload, data_ref=data_ref, max_versoes=120)


def carregar_snapshot_cranio_ai_summary(data_ref: date | None = None):
    return carregar_snapshot_do_historico(_snapshot_path(), data_ref=data_ref)


def carregar_snapshot_cranio_ai_summary_na_data_ou_anterior(data_ref: date | None = None):
    return carregar_snapshot_na_data_ou_anterior(_snapshot_path(), data_ref=data_ref)


def listar_datas_snapshot_cranio_ai_summary():
    return listar_datas_snapshot(_snapshot_path())
