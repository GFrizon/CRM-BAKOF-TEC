from __future__ import annotations

from datetime import datetime
from typing import Any

def _inicio_janela_mensal(periodo: str) -> tuple[datetime, int]:
    agora = datetime.now()
    periodo_norm = str(periodo or "ano_atual").strip().lower()
    if periodo_norm == "ultimos_365_dias":
        mes = agora.month - 11
        ano = agora.year
        while mes <= 0:
            mes += 12
            ano -= 1
        return datetime(ano, mes, 1), 12
    if periodo_norm == "ultimos_2_anos":
        mes = agora.month - 23
        ano = agora.year
        while mes <= 0:
            mes += 12
            ano -= 1
        return datetime(ano, mes, 1), 24
    if periodo_norm == "ultimos_3_anos":
        mes = agora.month - 35
        ano = agora.year
        while mes <= 0:
            mes += 12
            ano -= 1
        return datetime(ano, mes, 1), 36
    return datetime(agora.year, 1, 1), agora.month


def _avancar_mes(ano: int, mes: int) -> tuple[int, int]:
    if mes == 12:
        return ano + 1, 1
    return ano, mes + 1


def _montar_metricas_continuidade(meses_compra: set[tuple[int, int]], periodo: str) -> dict[str, Any]:
    inicio, meses_total = _inicio_janela_mensal(periodo)
    ano = inicio.year
    mes = inicio.month
    grade = []
    for _ in range(max(0, meses_total)):
        grade.append((ano, mes))
        ano, mes = _avancar_mes(ano, mes)

    meses_com_compra = sum(1 for chave in grade if chave in meses_compra)
    sequencia_recente = 0
    for chave in reversed(grade):
        if chave in meses_compra:
            sequencia_recente += 1
            continue
        if sequencia_recente > 0:
            break

    linha1 = f"{sequencia_recente} mes" if sequencia_recente == 1 else f"{sequencia_recente} meses"
    linha2 = "seguido" if sequencia_recente == 1 else "seguidos"
    tooltip = (
        f"Comprou em {meses_com_compra} de {meses_total} meses. "
        f"Sequencia recente: {sequencia_recente} mes(es)."
    )
    return {
        "sequencia_meses": sequencia_recente,
        "meses_com_compra": meses_com_compra,
        "meses_total_periodo": meses_total,
        "continuidade_compra_compacto": f"{linha1} {linha2}",
        "continuidade_compra_linha1": linha1,
        "continuidade_compra_linha2": linha2,
        "continuidade_compra_tooltip": tooltip,
    }


def obter_total_meses_periodo(periodo: str = "ano_atual") -> int:
    """Retorna quantos meses entram na janela analisada."""
    _, meses_total = _inicio_janela_mensal(periodo)
    return meses_total


def enriquecer_payloads_com_continuidade_compra(
    payloads: list[dict[str, Any]],
    periodo: str = "ano_atual",
    mapa_meses: dict[str, list[tuple[int, int]]] | None = None,
) -> None:
    codigos = []
    vistos = set()
    for payload in payloads or []:
        cd = str(payload.get("cd_cliente_oracle") or "").strip()
        if not cd or cd in vistos:
            continue
        vistos.add(cd)
        codigos.append(cd)

    if mapa_meses is None:
        from services.representante_metricas_cache_service import (
            carregar_meses_compra_representante,
        )

        mapa_meses = (
            carregar_meses_compra_representante(codigos, periodo=periodo)
            if codigos else {}
        )

    for payload in payloads or []:
        cd = str(payload.get("cd_cliente_oracle") or "").strip()
        meses = {tuple(m) for m in (mapa_meses.get(cd) or []) if m}
        metrica = _montar_metricas_continuidade(meses, periodo) if cd else {
            "sequencia_meses": 0,
            "meses_com_compra": 0,
            "meses_total_periodo": 12 if str(periodo or "").strip().lower() == "ultimos_365_dias" else datetime.now().month,
            "continuidade_compra_compacto": "",
            "continuidade_compra_linha1": "",
            "continuidade_compra_linha2": "",
            "continuidade_compra_tooltip": "",
        }
        payload.update(metrica)
