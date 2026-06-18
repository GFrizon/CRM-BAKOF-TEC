from datetime import datetime
from typing import Any, Iterable

from routes.clientes_ligacoes.grouping_stats import (
    calcular_stats_gerais_grupos,
    extrair_consultores_dos_grupos,
)
from routes.clientes_ligacoes.continuidade_compra import obter_total_meses_periodo

NOME_GRUPO_RECENCIA_LIVRE = "Lista livre por recorrencia"


def montar_rotulo_grupo_recencia(meses_com_compra: Any, periodo: str = "ano_atual") -> str:
    try:
        qtd = max(0, int(meses_com_compra or 0))
    except Exception:
        qtd = 0
    total_meses = obter_total_meses_periodo(periodo)
    return f"{qtd} de {total_meses} meses com compra"


def ordenar_clientes_recencia_frequencia(clientes_rep: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _valor_timestamp(cliente: dict[str, Any]) -> float:
        dt = cliente.get("ultimo_pedido_oracle")
        if not dt:
            return 0.0
        try:
            return float(dt.timestamp())
        except Exception:
            return 0.0

    return sorted(
        clientes_rep,
        key=lambda c: (
            int(c.get("sequencia_meses") or 0),
            int(c.get("meses_com_compra") or 0),
            _valor_timestamp(c),
            float(c.get("valor_total_365dias") or 0),
            float(c.get("valor_ultimo_pedido") or 0),
            str(c.get("nome") or ""),
        ),
        reverse=True,
    )


def consolidar_dados_grupos(
    *,
    representantes_data: dict[str, dict[str, Any]],
    chave_sem_grupo: str,
    conceitos_sem_conceito: Iterable[Any],
) -> tuple[list[tuple[str, dict[str, Any]]], list[str], int, dict[str, Any]]:
    for _, dados in representantes_data.items():
        clientes_rep = dados["clientes"]
        dados["total_clientes"] = len(clientes_rep)
        dados["liberados"] = sum(1 for c in clientes_rep if c.get("conceito") == "LIBERADO")
        dados["inadimplentes"] = sum(1 for c in clientes_rep if c.get("conceito") == "INADIMPLENTE")
        dados["sem_conceito"] = sum(
            1
            for c in clientes_rep
            if c.get("conceito") in conceitos_sem_conceito
        )

        valores = [c.get("valor_ultimo_pedido", 0) for c in clientes_rep if c.get("valor_ultimo_pedido")]
        dados["ticket_medio"] = sum(valores) / len(valores) if valores else 0

        hoje = datetime.now()
        dias_sem_pedido = []
        for c in clientes_rep:
            if c.get("ultimo_pedido_oracle"):
                dias = (hoje - c["ultimo_pedido_oracle"]).days
                dias_sem_pedido.append(dias)
        dados["dias_medio"] = sum(dias_sem_pedido) / len(dias_sem_pedido) if dias_sem_pedido else 0

        if dados.get("nome") == NOME_GRUPO_RECENCIA_LIVRE:
            dados["clientes"] = ordenar_clientes_recencia_frequencia(clientes_rep)
        else:
            dados["clientes"] = sorted(
                clientes_rep,
                key=lambda x: (
                    float(x.get("valor_total_365dias") or 0),
                    float(x.get("valor_ultimo_pedido") or 0),
                ),
                reverse=True,
            )

    possui_ordem_grupo = any("ordem_grupo" in dados for dados in representantes_data.values())
    if possui_ordem_grupo:
        representantes_ordenados = sorted(
            representantes_data.items(),
            key=lambda x: (
                -int(x[1].get("ordem_grupo", -1)),
                -x[1]["total_clientes"],
                x[0] == chave_sem_grupo,
                x[0],
            ),
        )
    else:
        representantes_ordenados = sorted(
            representantes_data.items(),
            key=lambda x: (-x[1]["total_clientes"], x[0] == chave_sem_grupo, x[0]),
        )
    consultores = extrair_consultores_dos_grupos(representantes_data)
    total, stats = calcular_stats_gerais_grupos(representantes_data)
    return representantes_ordenados, consultores, total, stats
