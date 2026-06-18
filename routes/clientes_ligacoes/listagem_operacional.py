import time
from datetime import datetime

from flask import current_app, render_template
from sqlalchemy.orm import selectinload

from core.models import Cliente, Ligacao, Usuario
from routes.clientes_ligacoes.agrupamento_view import montar_representantes_agrupados
from routes.clientes_ligacoes.badges import calcular_total_inativos_badge_com_cache
from routes.clientes_ligacoes.continuidade_compra import enriquecer_payloads_com_continuidade_compra
from routes.clientes_ligacoes.listagem_base_filters import (
    aplicar_filtro_base_clientes,
    aplicar_filtro_carteira_especial_consultor,
)
from routes.clientes_ligacoes.dashboard_operacional import (
    montar_meses_disponiveis,
    montar_stats_consultor_televendas,
    parse_filtro_mes_ano,
)
from routes.clientes_ligacoes.lista_operacional import filtrar_listas_por_termo, ordenar_clientes_por_aba
from routes.clientes_ligacoes.listagem_operacional_classificacao import classificar_listas_operacionais
from routes.clientes_ligacoes.pedido_andamento_helper import marcar_pedido_em_andamento_payloads
from routes.clientes_ligacoes.perf_logger import log_perf
from services.banner_service import get_banners_ativos

_OPERACIONAL_CLASSIFICACAO_CACHE = {}
_OPERACIONAL_CLASSIFICACAO_CACHE_TTL_SECONDS = 120


def _log_perf(label, started_at, **extra):
    log_perf(current_app, "meus-clientes/operacional", label, started_at, **extra)


def limpar_cache_operacional():
    _OPERACIONAL_CLASSIFICACAO_CACHE.clear()


def _cache_key_classificacao(current_user, apenas_meus, dashboard_tipo, codigos_representantes_vinculados):
    return (
        int(getattr(current_user, "id", 0) or 0),
        str(getattr(current_user, "tipo", "") or ""),
        bool(apenas_meus),
        str(dashboard_tipo or ""),
        tuple(sorted(str(c or "").strip() for c in (codigos_representantes_vinculados or []) if str(c or "").strip())),
    )


def _get_classificacao_cache(cache_key):
    item = _OPERACIONAL_CLASSIFICACAO_CACHE.get(cache_key)
    if not item:
        return None
    if (time.perf_counter() - item["ts"]) > _OPERACIONAL_CLASSIFICACAO_CACHE_TTL_SECONDS:
        _OPERACIONAL_CLASSIFICACAO_CACHE.pop(cache_key, None)
        return None
    return item["data"]


def _set_classificacao_cache(cache_key, pendentes, contatados, precisa_retornar):
    _OPERACIONAL_CLASSIFICACAO_CACHE[cache_key] = {
        "ts": time.perf_counter(),
        "data": (pendentes, contatados, precisa_retornar),
    }
    if len(_OPERACIONAL_CLASSIFICACAO_CACHE) > 32:
        itens = sorted(_OPERACIONAL_CLASSIFICACAO_CACHE.items(), key=lambda item: item[1]["ts"])
        _OPERACIONAL_CLASSIFICACAO_CACHE.clear()
        _OPERACIONAL_CLASSIFICACAO_CACHE.update(dict(itens[-24:]))


def render_fluxo_operacional(
    *,
    request,
    current_user,
    aba: str,
    total_oracle_badge: int,
    total_ativos_badge: int,
    total_proximos_badge: int,
    apenas_meus: bool,
    codigos_representantes_vinculados,
    cache_store,
    cache_ttl_seconds: int,
    total_construtoras_badge: int = 0,
    dashboard_tipo=None,
    visao=None,
    agrupar_por=None,
    periodo_recencia="ano_atual",
):
    perf_total = time.perf_counter()
    # Parametros de filtro mensal para consultores e televendas
    mes_filtro, ano_filtro = parse_filtro_mes_ano(request.args, current_user.tipo)

    q = (
        Cliente.query
        .options(
            # Evita explosao de linhas do joinedload com muitos historicos.
            selectinload(Cliente.ligacoes).load_only(
                Ligacao.id,
                Ligacao.consultor_id,
                Ligacao.data_hora,
                Ligacao.resultado,
            )
        )
        .filter(Cliente.ativo == True)
    )
    q = aplicar_filtro_base_clientes(
        query=q,
        current_user=current_user,
        apenas_meus=apenas_meus,
        dashboard_tipo=dashboard_tipo,
    )
    q = aplicar_filtro_carteira_especial_consultor(q, current_user)

    termo = request.args.get("q", "").strip()
    origem_filtro = ""
    cache_key = _cache_key_classificacao(
        current_user,
        apenas_meus,
        dashboard_tipo,
        codigos_representantes_vinculados,
    )
    perf_step = time.perf_counter()
    classificacao_cache = _get_classificacao_cache(cache_key)
    _log_perf("classificacao_cache", perf_step, hit=bool(classificacao_cache), aba=aba)
    if classificacao_cache:
        pendentes, contatados, precisa_retornar = classificacao_cache
    else:
        perf_step = time.perf_counter()
        clientes_todos = q.order_by(Cliente.nome.asc()).all()
        _log_perf("clientes_todos", perf_step, total=len(clientes_todos or []), aba=aba)

        perf_step = time.perf_counter()
        pendentes, contatados, precisa_retornar = classificar_listas_operacionais(
            clientes_todos=clientes_todos,
            current_user=current_user,
            aba=aba,
            codigos_representantes_vinculados=codigos_representantes_vinculados,
        )
        _log_perf(
            "classificar",
            perf_step,
            pendentes=len(pendentes),
            contatados=len(contatados),
            retornar=len(precisa_retornar),
        )
        perf_step = time.perf_counter()
        todos_para_enriquecer = pendentes + contatados + precisa_retornar
        enriquecer_payloads_com_continuidade_compra(todos_para_enriquecer, periodo="ano_atual")
        _log_perf("continuidade_compra_cache_fill", perf_step, total=len(todos_para_enriquecer))
        _set_classificacao_cache(cache_key, pendentes, contatados, precisa_retornar)
    total_pendentes_badge = len(pendentes)
    total_retornar_badge = len(precisa_retornar)
    total_retornos_atrasados = sum(
        1 for item in precisa_retornar if item.get("retorno_atrasado")
    )

    # Busca textual so na listagem atual (nao afeta badges).
    perf_step = time.perf_counter()
    pendentes_view, contatados_view, precisa_retornar_view = filtrar_listas_por_termo(
        termo,
        pendentes,
        contatados,
        precisa_retornar,
    )
    if origem_filtro in ("manual", "importado_csv"):
        pendentes_view = [c for c in pendentes_view if str(c.get("origem") or "").strip().lower() == origem_filtro]
        contatados_view = [c for c in contatados_view if str(c.get("origem") or "").strip().lower() == origem_filtro]
        precisa_retornar_view = [c for c in precisa_retornar_view if str(c.get("origem") or "").strip().lower() == origem_filtro]

    clientes = ordenar_clientes_por_aba(
        aba,
        pendentes_view,
        contatados_view,
        precisa_retornar_view,
        request.args.get("filtro"),
    )
    _log_perf("filtrar_ordenar", perf_step, total=len(clientes or []), termo=bool(termo))
    consultores = (
        Usuario.query
        .filter_by(tipo="consultor", ativo=True)
        .order_by(Usuario.nome.asc())
        .all() if current_user.tipo == "supervisor" else None
    )

    perf_step = time.perf_counter()
    stats = montar_stats_consultor_televendas(
        current_user,
        total_oracle_badge,
        total_ativos_badge,
    )
    _log_perf("stats_dashboard", perf_step, tipo=current_user.tipo)

    # Gerar lista de meses/anos disponiveis para o filtro do consultor e televendas
    meses_disponiveis_consultor = montar_meses_disponiveis(current_user.tipo)

    perf_step = time.perf_counter()
    total_inativos_badge = calcular_total_inativos_badge_com_cache(
        current_user=current_user,
        apenas_meus=apenas_meus,
        cache_store=cache_store,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    _log_perf("badge_inativos", perf_step, total=total_inativos_badge)

    # Para consultores: converter para vista agrupada por representante
    # (mantendo contatados/retornar na lista simples original).
    agrupar_pendentes_consultor = (
        current_user.tipo == "consultor"
        and aba == "pendentes"
        and agrupar_por != "sem_classificacao"
    )
    agrupar_pendentes_representante = (
        current_user.tipo == "representante"
        and aba == "pendentes"
    )
    agrupar_pendentes_supervisor = (current_user.tipo == "supervisor" and aba == "pendentes")
    usar_lazy_grupos = bool(
        agrupar_pendentes_consultor
        or agrupar_pendentes_representante
        or agrupar_pendentes_supervisor
    )
    usar_lazy_tabela = (not usar_lazy_grupos) and len(clientes or []) > 150
    if not usar_lazy_grupos and not usar_lazy_tabela:
        perf_step = time.perf_counter()
        marcar_pedido_em_andamento_payloads(clientes)
        _log_perf("pedido_em_andamento", perf_step, total=len(clientes or []))
    else:
        _log_perf("pedido_em_andamento", time.perf_counter(), total=0, skipped_lazy=True)
    if (
        agrupar_pendentes_consultor
        or agrupar_pendentes_representante
        or agrupar_pendentes_supervisor
        or (
            current_user.tipo in ("consultor", "supervisor", "supervisor_repr", "representante")
            and aba not in ("contatados", "retornar", "pendentes")
        )
    ):
        periodo_compra = periodo_recencia if agrupar_por == "recencia" else "ano_atual"
        perf_step = time.perf_counter()
        if periodo_compra != "ano_atual" or not classificacao_cache:
            enriquecer_payloads_com_continuidade_compra(
                clientes,
                periodo=periodo_compra,
            )
        _log_perf("continuidade_compra", perf_step, total=len(clientes or []))
        perf_step = time.perf_counter()
        representantes_ordenados_grp = montar_representantes_agrupados(
            clientes=clientes,
            tipo_usuario=current_user.tipo,
            aba=aba,
            agrupar_por=agrupar_por,
            periodo_recencia=periodo_recencia,
        )
        _log_perf("montar_grupos", perf_step, grupos=len(representantes_ordenados_grp or []))

        perf_step = time.perf_counter()
        response = render_template(
            "meus_clientes.html",
            representantes=representantes_ordenados_grp,
            usar_vista_agrupada=True,
            aba=aba,
            total_pendentes=total_pendentes_badge,
            total_retornar=total_retornar_badge,
            total_retornos_atrasados=total_retornos_atrasados,
            total_ativos=total_ativos_badge,
            total_inativos=total_inativos_badge,
            total_oracle=total_oracle_badge,
            total_proximos=total_proximos_badge,
            total_construtoras=total_construtoras_badge,
            is_supervisor=(current_user.tipo == "supervisor"),
            now=datetime.now,
            stats=stats,
            mostrar_novidades=not current_user.viu_novidades,
            banners_ativos=get_banners_ativos(),
            mes_filtro=mes_filtro,
            ano_filtro=ano_filtro,
            meses_disponiveis_consultor=meses_disponiveis_consultor,
            dashboard_tipo=dashboard_tipo,
            visao=visao,
            agrupar_por=agrupar_por,
            ano_recencia=datetime.now().year,
            periodo_recencia=periodo_recencia,
            usar_lazy_grupos=usar_lazy_grupos,
        )
        _log_perf("template_agrupado", perf_step, total=len(clientes or []))
        _log_perf("total", perf_total, aba=aba, visao=visao)
        return response

    clientes_template = clientes[:150] if usar_lazy_tabela else clientes
    perf_step = time.perf_counter()
    response = render_template(
        "meus_clientes.html",
        clientes=clientes_template,
        total_pendentes=total_pendentes_badge,
        total_retornar=total_retornar_badge,
        total_retornos_atrasados=total_retornos_atrasados,
        total_ativos=total_ativos_badge,
        total_inativos=total_inativos_badge,
        total_oracle=total_oracle_badge,
        total_proximos=total_proximos_badge,
        total_construtoras=total_construtoras_badge,
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
        visao=visao,
        agrupar_por=agrupar_por,
        ano_recencia=datetime.now().year,
        periodo_recencia=periodo_recencia,
        usar_lazy_tabela=usar_lazy_tabela,
        lazy_tabela_next_offset=len(clientes_template or []),
        lazy_tabela_total=len(clientes or []),
    )
    _log_perf("template_tabela", perf_step, total=len(clientes or []))
    _log_perf("total", perf_total, aba=aba, visao=visao)
    return response


def render_grupo_operacional_agrupado_html(
    *,
    request_args,
    current_user,
    aba: str,
    apenas_meus: bool,
    codigos_representantes_vinculados,
    dashboard_tipo=None,
    agrupar_por=None,
    periodo_recencia="ano_atual",
):
    grupo = (request_args.get("grupo") or "").strip()
    if not grupo or aba != "pendentes":
        return None
    try:
        offset = max(0, int(request_args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(request_args.get("limit") or 150)
    except (TypeError, ValueError):
        limit = 150
    limit = min(max(limit, 50), 500)

    q = (
        Cliente.query
        .options(
            selectinload(Cliente.ligacoes).load_only(
                Ligacao.id,
                Ligacao.consultor_id,
                Ligacao.data_hora,
                Ligacao.resultado,
            )
        )
        .filter(Cliente.ativo == True)
    )
    q = aplicar_filtro_base_clientes(
        query=q,
        current_user=current_user,
        apenas_meus=apenas_meus,
        dashboard_tipo=dashboard_tipo,
    )
    q = aplicar_filtro_carteira_especial_consultor(q, current_user)

    cache_key = _cache_key_classificacao(
        current_user,
        apenas_meus,
        dashboard_tipo,
        codigos_representantes_vinculados,
    )
    classificacao_cache = _get_classificacao_cache(cache_key)
    if classificacao_cache:
        pendentes, contatados, precisa_retornar = classificacao_cache
    else:
        clientes_todos = q.order_by(Cliente.nome.asc()).all()
        pendentes, contatados, precisa_retornar = classificar_listas_operacionais(
            clientes_todos=clientes_todos,
            current_user=current_user,
            aba=aba,
            codigos_representantes_vinculados=codigos_representantes_vinculados,
        )
        _set_classificacao_cache(cache_key, pendentes, contatados, precisa_retornar)

    termo = (request_args.get("q") or "").strip()
    origem_filtro = ""
    pendentes_view, contatados_view, precisa_retornar_view = filtrar_listas_por_termo(
        termo,
        pendentes,
        contatados,
        precisa_retornar,
    )
    if origem_filtro in ("manual", "importado_csv"):
        pendentes_view = [c for c in pendentes_view if str(c.get("origem") or "").strip().lower() == origem_filtro]
        contatados_view = [c for c in contatados_view if str(c.get("origem") or "").strip().lower() == origem_filtro]
        precisa_retornar_view = [c for c in precisa_retornar_view if str(c.get("origem") or "").strip().lower() == origem_filtro]

    clientes = ordenar_clientes_por_aba(
        aba,
        pendentes_view,
        contatados_view,
        precisa_retornar_view,
        request_args.get("filtro"),
    )
    periodo_compra = periodo_recencia if agrupar_por == "recencia" else "ano_atual"
    enriquecer_payloads_com_continuidade_compra(clientes, periodo=periodo_compra)
    representantes = montar_representantes_agrupados(
        clientes=clientes,
        tipo_usuario=current_user.tipo,
        aba=aba,
        agrupar_por=agrupar_por,
        periodo_recencia=periodo_recencia,
    )
    grupo_encontrado = None
    for nome, dados in representantes:
        if str(nome or "").strip() == grupo:
            grupo_encontrado = (nome, dados)
            break
    if not grupo_encontrado:
        return ""

    nome_grupo, dados_grupo = grupo_encontrado
    clientes_grupo = dados_grupo.get("clientes") or []
    total_grupo = len(clientes_grupo)
    clientes_pagina = clientes_grupo[offset:offset + limit]
    dados_pagina = {
        **dados_grupo,
        "clientes": clientes_pagina,
    }
    marcar_pedido_em_andamento_payloads(clientes_pagina)
    next_offset = offset + len(clientes_pagina)
    return render_template(
        "meus_clientes/_lista_agrupada.html",
        representantes=[(nome_grupo, dados_pagina)],
        usar_lazy_grupos=False,
        usar_vista_agrupada=True,
        aba=aba,
        is_supervisor=(current_user.tipo == "supervisor"),
        now=datetime.now,
        dashboard_tipo=dashboard_tipo,
        visao=request_args.get("visao"),
        agrupar_por=agrupar_por,
        ano_recencia=datetime.now().year,
        periodo_recencia=periodo_recencia,
        lazy_next_offset=next_offset,
        lazy_has_more=next_offset < total_grupo,
        lazy_total=total_grupo,
    )


def render_tabela_operacional_html(
    *,
    request_args,
    current_user,
    aba: str,
    apenas_meus: bool,
    codigos_representantes_vinculados,
    dashboard_tipo=None,
):
    try:
        offset = max(0, int(request_args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(request_args.get("limit") or 150)
    except (TypeError, ValueError):
        limit = 150
    limit = min(max(limit, 50), 500)

    q = (
        Cliente.query
        .options(
            selectinload(Cliente.ligacoes).load_only(
                Ligacao.id,
                Ligacao.consultor_id,
                Ligacao.data_hora,
                Ligacao.resultado,
            )
        )
        .filter(Cliente.ativo == True)
    )
    q = aplicar_filtro_base_clientes(
        query=q,
        current_user=current_user,
        apenas_meus=apenas_meus,
        dashboard_tipo=dashboard_tipo,
    )
    q = aplicar_filtro_carteira_especial_consultor(q, current_user)

    cache_key = _cache_key_classificacao(
        current_user,
        apenas_meus,
        dashboard_tipo,
        codigos_representantes_vinculados,
    )
    classificacao_cache = _get_classificacao_cache(cache_key)
    if classificacao_cache:
        pendentes, contatados, precisa_retornar = classificacao_cache
    else:
        clientes_todos = q.order_by(Cliente.nome.asc()).all()
        pendentes, contatados, precisa_retornar = classificar_listas_operacionais(
            clientes_todos=clientes_todos,
            current_user=current_user,
            aba=aba,
            codigos_representantes_vinculados=codigos_representantes_vinculados,
        )
        _set_classificacao_cache(cache_key, pendentes, contatados, precisa_retornar)

    termo = (request_args.get("q") or "").strip()
    origem_filtro = ""
    pendentes_view, contatados_view, precisa_retornar_view = filtrar_listas_por_termo(
        termo,
        pendentes,
        contatados,
        precisa_retornar,
    )
    if origem_filtro in ("manual", "importado_csv"):
        pendentes_view = [c for c in pendentes_view if str(c.get("origem") or "").strip().lower() == origem_filtro]
        contatados_view = [c for c in contatados_view if str(c.get("origem") or "").strip().lower() == origem_filtro]
        precisa_retornar_view = [c for c in precisa_retornar_view if str(c.get("origem") or "").strip().lower() == origem_filtro]

    clientes = ordenar_clientes_por_aba(
        aba,
        pendentes_view,
        contatados_view,
        precisa_retornar_view,
        request_args.get("filtro"),
    )
    total = len(clientes or [])
    clientes_pagina = clientes[offset:offset + limit]
    marcar_pedido_em_andamento_payloads(clientes_pagina)
    next_offset = offset + len(clientes_pagina)
    return render_template(
        "meus_clientes/_tabela_clientes_padrao.html",
        clientes=clientes_pagina,
        aba=aba,
        now=datetime.now,
        usar_lazy_tabela=next_offset < total,
        lazy_tabela_next_offset=next_offset,
        lazy_tabela_total=total,
    )
