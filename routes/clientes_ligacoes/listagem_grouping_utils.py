from datetime import datetime

from routes.clientes_ligacoes.grouping_stats import (
    calcular_stats_gerais_grupos,
    extrair_consultores_dos_grupos,
)


def consolidar_dados_grupos(
    *,
    representantes_data: dict,
    chave_sem_grupo: str,
    conceitos_sem_conceito,
):
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

        dados["clientes"] = sorted(
            clientes_rep,
            key=lambda x: (
                float(x.get("valor_total_365dias") or 0),
                float(x.get("valor_ultimo_pedido") or 0),
            ),
            reverse=True,
        )

    representantes_ordenados = sorted(
        representantes_data.items(),
        key=lambda x: (-x[1]["total_clientes"], x[0] == chave_sem_grupo, x[0]),
    )
    consultores = extrair_consultores_dos_grupos(representantes_data)
    total, stats = calcular_stats_gerais_grupos(representantes_data)
    return representantes_ordenados, consultores, total, stats
