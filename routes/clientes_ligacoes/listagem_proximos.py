from flask import render_template

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
):
    representantes_ordenados_px, total_proximos_count, stats_proximos = (
        preparar_contexto_proximos_inativacao(
            current_user,
            codigos_representantes_vinculados,
        )
    )

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
    )
