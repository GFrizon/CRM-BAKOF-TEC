from flask import render_template

from routes.clientes_ligacoes.listagem_filters import corresponde_termo_busca
from routes.clientes_ligacoes.proximos_tab import preparar_contexto_proximos_inativacao
from routes.clientes_ligacoes.proximos_totais import calcular_totais_abas_proximos


def render_aba_proximos_inativacao(
    *,
    aba: str,
    current_user,
    codigos_representantes_vinculados,
    total_oracle_badge: int,
    total_inativos_badge: int,
    q: str,
    dashboard_tipo=None,
    visao=None,
    agrupar_por="representante",
):
    agrupar_por_ativo = agrupar_por if agrupar_por in ("representante", "uf") else None
    representantes_ordenados_px, total_proximos_count, stats_proximos = (
        preparar_contexto_proximos_inativacao(
            current_user,
            codigos_representantes_vinculados,
            agrupar_por=agrupar_por_ativo,
        )
    )
    termo = (q or "").strip()
    if termo:
        representantes_filtrados = []
        for nome_grupo, dados_grupo in representantes_ordenados_px:
            clientes_filtrados = [
                cliente
                for cliente in dados_grupo.get("clientes", [])
                if corresponde_termo_busca(
                    termo,
                    cliente,
                    ("nome", "cnpj", "telefone", "telefone2", "representante_nome", "representante_oracle", "contato"),
                )
            ]
            if not clientes_filtrados:
                continue
            dados_novo = dict(dados_grupo)
            dados_novo["clientes"] = clientes_filtrados
            dados_novo["total_clientes"] = len(clientes_filtrados)
            dados_novo["liberados"] = sum(1 for c in clientes_filtrados if (c.get("conceito") or "").upper() == "LIBERADO")
            dados_novo["inadimplentes"] = sum(1 for c in clientes_filtrados if (c.get("conceito") or "").upper() == "INADIMPLENTE")
            dados_novo["sem_conceito"] = sum(
                1 for c in clientes_filtrados
                if (c.get("conceito") or "").strip().upper() in ("", "SEM CONCEITO")
            )
            valores = [float(c.get("valor_ultimo_pedido") or 0) for c in clientes_filtrados if c.get("valor_ultimo_pedido")]
            dias = [int(c.get("dias_sem_pedido") or 0) for c in clientes_filtrados]
            dados_novo["ticket_medio"] = (sum(valores) / len(valores)) if valores else 0
            dados_novo["dias_medio"] = (sum(dias) / len(dias)) if dias else 0
            representantes_filtrados.append((nome_grupo, dados_novo))

        representantes_ordenados_px = representantes_filtrados
        total_proximos_count = sum(len(dados.get("clientes", [])) for _, dados in representantes_ordenados_px)
        total_liberados = sum((dados.get("liberados") or 0) for _, dados in representantes_ordenados_px)
        total_inadimplentes = sum((dados.get("inadimplentes") or 0) for _, dados in representantes_ordenados_px)
        total_sem_conceito = sum((dados.get("sem_conceito") or 0) for _, dados in representantes_ordenados_px)
        ticket_lista = [
            float(c.get("valor_ultimo_pedido") or 0)
            for _, dados in representantes_ordenados_px
            for c in dados.get("clientes", [])
            if c.get("valor_ultimo_pedido")
        ]
        dias_lista = [
            int(c.get("dias_sem_pedido") or 0)
            for _, dados in representantes_ordenados_px
            for c in dados.get("clientes", [])
        ]
        stats_proximos = {
            "liberados": total_liberados,
            "inadimplentes": total_inadimplentes,
            "sem_conceito": total_sem_conceito,
            "ticket_medio": (sum(ticket_lista) / len(ticket_lista)) if ticket_lista else 0,
            "dias_sem_pedido": int((sum(dias_lista) / len(dias_lista))) if dias_lista else 0,
            "perc_liberados": round((total_liberados / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
            "perc_inadimplentes": round((total_inadimplentes / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
            "perc_sem_conceito": round((total_sem_conceito / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
        }

    total_pendentes_px, total_contatados_px, total_retornar_px = calcular_totais_abas_proximos(
        current_user,
        codigos_representantes_vinculados,
    )

    return render_template(
        "meus_clientes.html",
        representantes=representantes_ordenados_px,
        aba=aba,
        total_pendentes=total_pendentes_px,
        total_contatados=total_contatados_px,
        total_retornar=total_retornar_px,
        total_oracle=total_oracle_badge,
        total_inativos=total_inativos_badge,
        total_proximos=total_proximos_count,
        usar_vista_agrupada=True,
        is_supervisor=(current_user.tipo == "supervisor"),
        stats={},
        stats_proximos=stats_proximos,
        q=q,
        meses_disponiveis_consultor=[],
        mes_filtro=None,
        ano_filtro=None,
        dashboard_tipo=dashboard_tipo,
        visao=visao,
        agrupar_por=(agrupar_por_ativo or "representante"),
    )
