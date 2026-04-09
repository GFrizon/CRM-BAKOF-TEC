from datetime import datetime

from flask import render_template
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from core.extensions import db
from core.models import Cliente, Ligacao, Usuario
from routes.clientes_ligacoes.agrupamento_view import montar_representantes_agrupados
from routes.clientes_ligacoes.badges import calcular_total_inativos_badge_com_cache
from routes.clientes_ligacoes.dashboard_operacional import (
    montar_meses_disponiveis,
    montar_stats_consultor_televendas,
    parse_filtro_mes_ano,
)
from routes.clientes_ligacoes.lista_operacional import filtrar_listas_por_termo, ordenar_clientes_por_aba
from routes.clientes_ligacoes.listagem_operacional_classificacao import classificar_listas_operacionais
from services.banner_service import get_banners_ativos


def render_fluxo_operacional(
    *,
    request,
    current_user,
    aba: str,
    total_oracle_badge: int,
    total_proximos_badge: int,
    apenas_meus: bool,
    codigos_representantes_vinculados,
    cache_store,
    cache_ttl_seconds: int,
    dashboard_tipo=None,
):
    # Parametros de filtro mensal para consultores e televendas
    mes_filtro, ano_filtro = parse_filtro_mes_ano(request.args, current_user.tipo)

    q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(Cliente.ativo == True)
    if current_user.tipo == "supervisor" and dashboard_tipo in ("consultor", "televendas"):
        operadores_ids_query = (
            db.session.query(Usuario.id)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
        )
        q = q.filter(Cliente.consultor_id.in_(operadores_ids_query))

    if current_user.tipo == "televendas":
        clientes_ligados_por_tv = (
            db.session.query(Ligacao.cliente_id)
            .filter(Ligacao.consultor_id == current_user.id)
            .distinct()
        )
        q = q.filter(
            or_(
                Cliente.consultor_id == current_user.id,
                Cliente.id.in_(clientes_ligados_por_tv),
            )
        )
    elif apenas_meus:
        q = q.filter(Cliente.consultor_id == current_user.id)

    termo = request.args.get("q", "").strip()
    clientes_todos = q.order_by(Cliente.nome.asc()).all()

    pendentes, contatados, precisa_retornar = classificar_listas_operacionais(
        clientes_todos=clientes_todos,
        current_user=current_user,
        aba=aba,
        codigos_representantes_vinculados=codigos_representantes_vinculados,
    )

    total_pendentes_badge = len(pendentes)
    total_contatados_badge = len(contatados)
    total_retornar_badge = len(precisa_retornar)

    # Busca textual so na listagem atual (nao afeta badges).
    pendentes_view, contatados_view, precisa_retornar_view = filtrar_listas_por_termo(
        termo,
        pendentes,
        contatados,
        precisa_retornar,
    )
    clientes = ordenar_clientes_por_aba(
        aba,
        pendentes_view,
        contatados_view,
        precisa_retornar_view,
        request.args.get("filtro"),
    )

    consultores = (
        Usuario.query
        .filter_by(tipo="consultor", ativo=True)
        .order_by(Usuario.nome.asc())
        .all() if current_user.tipo == "supervisor" else None
    )

    stats = montar_stats_consultor_televendas(current_user, total_oracle_badge)

    # Gerar lista de meses/anos disponiveis para o filtro do consultor e televendas
    meses_disponiveis_consultor = montar_meses_disponiveis(current_user.tipo)

    total_inativos_badge = calcular_total_inativos_badge_com_cache(
        current_user=current_user,
        apenas_meus=apenas_meus,
        cache_store=cache_store,
        cache_ttl_seconds=cache_ttl_seconds,
    )

    # Para consultores: converter para vista agrupada por representante
    # (mantendo contatados/retornar na lista simples original).
    if (
        (current_user.tipo in ("supervisor", "consultor") and aba == "pendentes")
        or (
            current_user.tipo in ("consultor", "supervisor", "supervisor_repr")
            and aba not in ("contatados", "retornar", "pendentes")
        )
    ):
        representantes_ordenados_grp = montar_representantes_agrupados(
            clientes=clientes,
            tipo_usuario=current_user.tipo,
            aba=aba,
        )

        return render_template(
            "meus_clientes.html",
            representantes=representantes_ordenados_grp,
            usar_vista_agrupada=True,
            aba=aba,
            total_pendentes=total_pendentes_badge,
            total_contatados=total_contatados_badge,
            total_retornar=total_retornar_badge,
            total_inativos=total_inativos_badge,
            total_oracle=total_oracle_badge,
            total_proximos=total_proximos_badge,
            is_supervisor=(current_user.tipo == "supervisor"),
            now=datetime.now,
            stats=stats,
            mostrar_novidades=not current_user.viu_novidades,
            banners_ativos=get_banners_ativos(),
            mes_filtro=mes_filtro,
            ano_filtro=ano_filtro,
            meses_disponiveis_consultor=meses_disponiveis_consultor,
            dashboard_tipo=dashboard_tipo,
        )

    return render_template(
        "meus_clientes.html",
        clientes=clientes,
        total_pendentes=total_pendentes_badge,
        total_contatados=total_contatados_badge,
        total_retornar=total_retornar_badge,
        total_inativos=total_inativos_badge,
        total_oracle=total_oracle_badge,
        total_proximos=total_proximos_badge,
        aba=aba,
        is_supervisor=(current_user.tipo == "supervisor"),
        now=datetime.now,
        consultores=consultores,
        stats=stats,
        mostrar_novidades=not current_user.viu_novidades,
        banners_ativos=get_banners_ativos(),
        mes_filtro=mes_filtro,
        ano_filtro=ano_filtro,
        meses_disponiveis_consultor=meses_disponiveis_consultor,
        dashboard_tipo=dashboard_tipo,
    )
