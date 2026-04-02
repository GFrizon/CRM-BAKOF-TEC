from datetime import datetime


def extrair_consultores_dos_grupos(grupos: dict):
    consultores_set = set()
    for dados in grupos.values():
        for c in dados.get("clientes", []):
            if c.get("categoria_consultor"):
                consultores_set.add(c.get("categoria_consultor"))
    return [{"nome": nome} for nome in sorted(consultores_set)]


def calcular_stats_gerais_grupos(grupos: dict):
    total_clientes = 0
    total_liberados = 0
    total_inadimplentes = 0
    total_sem_conceito = 0
    todos_valores = []
    todos_dias = []

    agora = datetime.now()
    for dados in grupos.values():
        clientes_rep = dados.get("clientes", [])
        total_clientes += len(clientes_rep)
        total_liberados += int(dados.get("liberados") or 0)
        total_inadimplentes += int(dados.get("inadimplentes") or 0)
        total_sem_conceito += int(dados.get("sem_conceito") or 0)

        for c in clientes_rep:
            if c.get("valor_ultimo_pedido"):
                todos_valores.append(c.get("valor_ultimo_pedido"))
            if c.get("ultimo_pedido_oracle"):
                todos_dias.append((agora - c["ultimo_pedido_oracle"]).days)

    ticket_medio = sum(todos_valores) / len(todos_valores) if todos_valores else 0
    dias_medio = sum(todos_dias) / len(todos_dias) if todos_dias else 0

    stats = {
        "liberados": total_liberados,
        "inadimplentes": total_inadimplentes,
        "sem_conceito": total_sem_conceito,
        "ticket_medio": ticket_medio,
        "dias_sem_pedido": int(dias_medio),
        "perc_liberados": round((total_liberados / total_clientes) * 100, 1) if total_clientes > 0 else 0,
        "perc_inadimplentes": round((total_inadimplentes / total_clientes) * 100, 1) if total_clientes > 0 else 0,
        "perc_sem_conceito": round((total_sem_conceito / total_clientes) * 100, 1) if total_clientes > 0 else 0,
    }

    return total_clientes, stats
