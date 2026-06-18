from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from services.cranio_insights_service import gerar_insights_por_visao
from services.daily_snapshot_history import (
    carregar_snapshot_do_historico,
    carregar_snapshot_na_data_ou_anterior,
    listar_datas_snapshot,
    salvar_snapshot_em_historico,
)


def _snapshot_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "cranio_insights_snapshot.json"


def salvar_snapshot_cranio_insights(payload: dict, data_ref: date | None = None):
    return salvar_snapshot_em_historico(_snapshot_path(), payload, data_ref=data_ref, max_versoes=120)


def carregar_snapshot_cranio_insights(data_ref: date | None = None):
    return carregar_snapshot_do_historico(_snapshot_path(), data_ref=data_ref)


def carregar_snapshot_cranio_insights_na_data_ou_anterior(data_ref: date | None = None):
    return carregar_snapshot_na_data_ou_anterior(_snapshot_path(), data_ref=data_ref)


def listar_datas_snapshot_cranio_insights():
    return listar_datas_snapshot(_snapshot_path())


def gerar_payload_snapshot_cranio(data_ref: date | None = None):
    ref = data_ref or datetime.now().date()
    return {
        "data_ref": ref.isoformat(),
        "gerado_em": datetime.now().isoformat(),
        "visoes": {
            "consultores": {
                "hoje": gerar_insights_por_visao("consultores", "hoje"),
                "3d": gerar_insights_por_visao("consultores", "3d"),
                "mes": gerar_insights_por_visao("consultores", "mes"),
            },
            "televendas": {
                "hoje": gerar_insights_por_visao("televendas", "hoje"),
                "3d": gerar_insights_por_visao("televendas", "3d"),
                "mes": gerar_insights_por_visao("televendas", "mes"),
            },
        },
    }


def gerar_e_salvar_snapshot_cranio(data_ref: date | None = None):
    payload = gerar_payload_snapshot_cranio(data_ref=data_ref)
    return salvar_snapshot_cranio_insights(payload, data_ref=data_ref)
