import time
from datetime import datetime

from flask import jsonify, render_template, request
from flask_login import current_user
from sqlalchemy.orm import joinedload

from core.extensions import db
from core.models import Cliente, Ligacao
from routes.clientes_ligacoes.badges import calcular_total_inativos_badge_com_cache
from routes.clientes_ligacoes.listagem_access import preparar_contexto_inicial_listagem
from routes.clientes_ligacoes.listagem_base_filters import (
    aplicar_filtro_base_clientes,
    aplicar_filtro_carteira_especial_consultor,
)
from routes.clientes_ligacoes.listagem_ativos import render_aba_ativos
from routes.clientes_ligacoes.listagem_inativos import render_aba_inativos
from routes.clientes_ligacoes.listagem_construtoras import render_aba_construtoras
from routes.clientes_ligacoes.listagem_operacional import (
    render_fluxo_operacional,
    render_grupo_operacional_agrupado_html,
    render_tabela_operacional_html,
)
from routes.clientes_ligacoes.listagem_operacional_classificacao import classificar_listas_operacionais
from routes.clientes_ligacoes.listagem_oracle import render_aba_oracle
from routes.clientes_ligacoes.listagem_proximos import render_aba_proximos_inativacao
from routes.clientes_ligacoes.perf_logger import log_perf
from routes.clientes_ligacoes.dashboard_operacional import (
    montar_meses_disponiveis,
    montar_stats_consultor_televendas,
    parse_filtro_mes_ano,
)
from services.banner_service import get_banners_ativos

_INATIVOS_COUNT_CACHE = {}
_INATIVOS_COUNT_CACHE_TTL_SECONDS = 600
_REPRESENTANTE_DASHBOARD_CACHE = {}
_REPRESENTANTE_DASHBOARD_CACHE_TTL_SECONDS = 300


def limpar_cache_contagem_inativos():
    _INATIVOS_COUNT_CACHE.clear()
    _REPRESENTANTE_DASHBOARD_CACHE.clear()


def _log_perf(app, label, started_at, **extra):
    log_perf(app, "meus-clientes", label, started_at, **extra)


def _cache_representante_dashboard_get(codigo_representante):
    chave = str(codigo_representante or "").strip()
    if not chave:
        return None
    cache = _REPRESENTANTE_DASHBOARD_CACHE.get(chave)
    if not cache or not cache.get("ts"):
        return None
    idade = (datetime.now() - cache["ts"]).total_seconds()
    if idade > _REPRESENTANTE_DASHBOARD_CACHE_TTL_SECONDS:
        return None
    return {
        "total_pendentes": int(cache.get("total_pendentes") or 0),
        "total_retornar": int(cache.get("total_retornar") or 0),
    }


def _cache_representante_dashboard_set(codigo_representante, total_pendentes, total_retornar):
    chave = str(codigo_representante or "").strip()
    if not chave:
        return
    _REPRESENTANTE_DASHBOARD_CACHE[chave] = {
        "total_pendentes": int(total_pendentes or 0),
        "total_retornar": int(total_retornar or 0),
        "ts": datetime.now(),
    }


def _calcular_retornos_atrasados(current_user, apenas_meus, dashboard_tipo=None):
    q = Cliente.query.filter(
        Cliente.ativo == True,
        Cliente.proxima_ligacao.isnot(None),
        Cliente.proxima_ligacao <= datetime.now(),
    )
    q = aplicar_filtro_base_clientes(
        query=q,
        current_user=current_user,
        apenas_meus=apenas_meus,
        dashboard_tipo=dashboard_tipo,
    )
    q = aplicar_filtro_carteira_especial_consultor(q, current_user)
    return q.count()


def register_clientes_ligacoes_listagem_routes(app):
    _PERIODO_PADRAO_ABA = {
        "oracle": "ultimos_365_dias",
    }

    def _resolver_periodo_recencia(aba_ctx=""):
        valor = (request.args.get("periodo_recencia") or "").strip().lower()
        if valor in ("ano_atual", "ultimos_365_dias", "ultimos_2_anos", "ultimos_3_anos"):
            return valor
        return _PERIODO_PADRAO_ABA.get(str(aba_ctx or ""), "ano_atual")

    def _resolver_agrupar_por(aba_atual: str):
        valor = (request.args.get("agrupar_por") or "").strip().lower()
        if current_user.tipo == "supervisor" and aba_atual == "pendentes":
            if valor in ("representante", "consultor", "recencia"):
                return valor
            return "consultor"
        if (
            valor == "sem_classificacao"
            and current_user.tipo == "consultor"
            and aba_atual == "pendentes"
        ):
            return valor
        if (
            valor == "recencia"
            and current_user.tipo == "consultor"
            and aba_atual == "pendentes"
        ):
            return valor
        if valor in ("representante", "uf", "recencia"):
            return valor
        if valor == "consultor" and current_user.tipo == "supervisor":
            return valor
        # Mantem o padrao atual ao abrir:
        # inativos por UF, demais por representante.
        return "uf" if aba_atual == "inativos" else "representante"

    def _calcular_badges_operacionais(aba, apenas_meus, codigos_representantes_vinculados, dashboard_tipo=None):
        from datetime import timedelta
        from sqlalchemy import and_

        q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(Cliente.ativo == True)
        q = aplicar_filtro_base_clientes(
            query=q,
            current_user=current_user,
            apenas_meus=apenas_meus,
            dashboard_tipo=dashboard_tipo,
        )
        q = aplicar_filtro_carteira_especial_consultor(q, current_user)

        # Para consultor, excluir 90-150d da contagem operacional para manter badge consistente
        if current_user.tipo == "consultor":
            limite_min_90_150 = datetime.now() - timedelta(days=150)
            limite_max_90_150 = datetime.now() - timedelta(days=90)
            q = q.filter(
                ~and_(
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_min_90_150, limite_max_90_150),
                )
            )

        clientes_todos = q.order_by(Cliente.nome.asc()).all()
        pendentes, contatados, precisa_retornar = classificar_listas_operacionais(
            clientes_todos=clientes_todos,
            current_user=current_user,
            aba=aba,
            codigos_representantes_vinculados=codigos_representantes_vinculados,
        )
        return len(pendentes), len(precisa_retornar)

    def _render_dashboard_only(
        *,
        contexto_inicial,
        total_inativos_badge,
        agrupar_por,
        periodo_recencia,
        total_retornos_atrasados_badge=0,
    ):
        perf_total_dashboard = time.perf_counter()
        mes_filtro, ano_filtro = parse_filtro_mes_ano(request.args, current_user.tipo)
        perf_step = time.perf_counter()
        stats = montar_stats_consultor_televendas(
            current_user,
            contexto_inicial["total_oracle_badge"],
            contexto_inicial.get("total_ativos_badge", 0),
        )
        _log_perf(app, "dashboard_stats", perf_step, tipo=getattr(current_user, "tipo", None))
        total_pendentes = 0
        total_retornar = 0
        if current_user.tipo in ("supervisor_repr", "representante"):
            perf_step = time.perf_counter()
            if current_user.tipo == "representante":
                codigo_representante = getattr(current_user, "codigo_representante", "")
                cache = _cache_representante_dashboard_get(codigo_representante)
                if cache is not None:
                    total_pendentes = cache["total_pendentes"]
                    total_retornar = 0
                else:
                    total_pendentes, _ = _calcular_badges_operacionais(
                        aba=contexto_inicial["aba"],
                        apenas_meus=contexto_inicial["apenas_meus"],
                        codigos_representantes_vinculados=contexto_inicial["codigos_representantes_vinculados"],
                        dashboard_tipo=contexto_inicial["dashboard_tipo"],
                    )
                    total_retornar = 0
                    _cache_representante_dashboard_set(
                        codigo_representante,
                        total_pendentes,
                        total_retornar,
                    )
            else:
                total_pendentes, total_retornar = _calcular_badges_operacionais(
                    aba=contexto_inicial["aba"],
                    apenas_meus=contexto_inicial["apenas_meus"],
                    codigos_representantes_vinculados=contexto_inicial["codigos_representantes_vinculados"],
                    dashboard_tipo=contexto_inicial["dashboard_tipo"],
                )
            _log_perf(app, "dashboard_totais_representante", perf_step)
        perf_step = time.perf_counter()
        response = render_template(
            "meus_clientes.html",
            aba=contexto_inicial["aba"],
            visao=contexto_inicial["visao"],
            dashboard_tipo=contexto_inicial["dashboard_tipo"],
            total_pendentes=total_pendentes,
            total_retornar=total_retornar,
            total_oracle=contexto_inicial["total_oracle_badge"],
            total_ativos=contexto_inicial.get("total_ativos_badge", 0),
            total_inativos=total_inativos_badge,
            total_proximos=contexto_inicial["total_proximos_badge"],
            total_construtoras=contexto_inicial.get("total_construtoras_badge", 0),
            total_retornos_atrasados=total_retornos_atrasados_badge,
            stats=stats,
            is_supervisor=(current_user.tipo == "supervisor"),
            mostrar_novidades=not current_user.viu_novidades,
            banners_ativos=get_banners_ativos(),
            mes_filtro=mes_filtro,
            ano_filtro=ano_filtro,
            meses_disponiveis_consultor=montar_meses_disponiveis(current_user.tipo),
            agrupar_por=agrupar_por,
            periodo_recencia=periodo_recencia,
            ano_recencia=datetime.now().year,
        )
        _log_perf(app, "dashboard_template", perf_step)
        _log_perf(app, "dashboard_total", perf_total_dashboard, tipo=getattr(current_user, "tipo", None))
        return response

    @app.route('/meus-clientes')
    def meus_clientes():
        perf_total = time.perf_counter()
        perf_step = time.perf_counter()
        contexto_inicial = preparar_contexto_inicial_listagem(request, current_user)
        _log_perf(
            app,
            "contexto_inicial",
            perf_step,
            user_id=getattr(current_user, "id", None),
            tipo=getattr(current_user, "tipo", None),
        )
        if contexto_inicial.get("response") is not None:
            _log_perf(app, "total_redirect", perf_total)
            return contexto_inicial["response"]
        aba = contexto_inicial["aba"]
        visao = contexto_inicial["visao"]
        dashboard_tipo = contexto_inicial["dashboard_tipo"]
        total_oracle_badge = contexto_inicial["total_oracle_badge"]
        total_proximos_badge = contexto_inicial["total_proximos_badge"]
        total_ativos_badge = contexto_inicial["total_ativos_badge"]
        total_construtoras_badge = contexto_inicial.get("total_construtoras_badge", 0)
        apenas_meus = contexto_inicial["apenas_meus"]
        codigos_representantes_vinculados = contexto_inicial["codigos_representantes_vinculados"]
        agrupar_por = _resolver_agrupar_por(aba)
        periodo_recencia = _resolver_periodo_recencia(aba)
        perf_step = time.perf_counter()
        total_inativos_badge = calcular_total_inativos_badge_com_cache(
            current_user=current_user,
            apenas_meus=apenas_meus,
            cache_store=_INATIVOS_COUNT_CACHE,
            cache_ttl_seconds=_INATIVOS_COUNT_CACHE_TTL_SECONDS,
        )
        _log_perf(
            app,
            "badge_inativos",
            perf_step,
            aba=aba,
            visao=visao,
            total=total_inativos_badge,
        )
        perf_step = time.perf_counter()
        if current_user.tipo == "representante":
            total_retornos_atrasados_badge = 0
        else:
            total_retornos_atrasados_badge = _calcular_retornos_atrasados(
                current_user=current_user,
                apenas_meus=apenas_meus,
                dashboard_tipo=dashboard_tipo,
            )
        _log_perf(
            app,
            "badge_retornos_atrasados",
            perf_step,
            total=total_retornos_atrasados_badge,
        )

        if visao == "dashboard":
            perf_step = time.perf_counter()
            response = _render_dashboard_only(
                contexto_inicial=contexto_inicial,
                total_inativos_badge=total_inativos_badge,
                total_retornos_atrasados_badge=total_retornos_atrasados_badge,
                agrupar_por=agrupar_por,
                periodo_recencia=periodo_recencia,
            )
            _log_perf(app, "render_dashboard_only", perf_step, aba=aba)
            _log_perf(app, "total", perf_total, aba=aba, visao=visao)
            return response

        if aba == 'oracle':
            perf_step = time.perf_counter()
            response = render_aba_oracle(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                apenas_meus=apenas_meus,
                total_oracle_badge=total_oracle_badge,
                total_ativos_badge=total_ativos_badge,
                total_inativos_badge=total_inativos_badge,
                total_proximos_badge=total_proximos_badge,
                total_construtoras_badge=total_construtoras_badge,
                total_retornos_atrasados_badge=total_retornos_atrasados_badge,
                dashboard_tipo=dashboard_tipo,
                visao=visao,
                agrupar_por=agrupar_por,
                periodo_recencia=periodo_recencia,
            )
            _log_perf(app, "render_aba_oracle", perf_step, visao=visao)
            _log_perf(app, "total", perf_total, aba=aba, visao=visao)
            return response

        if aba == 'ativos':
            perf_step = time.perf_counter()
            response = render_aba_ativos(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                apenas_meus=apenas_meus,
                total_oracle_badge=total_oracle_badge,
                total_ativos_badge=total_ativos_badge,
                total_inativos_badge=total_inativos_badge,
                total_proximos_badge=total_proximos_badge,
                total_construtoras_badge=total_construtoras_badge,
                total_retornos_atrasados_badge=total_retornos_atrasados_badge,
                dashboard_tipo=dashboard_tipo,
                visao=visao,
                agrupar_por=agrupar_por,
                periodo_recencia=periodo_recencia,
            )
            _log_perf(app, "render_aba_ativos", perf_step, visao=visao)
            _log_perf(app, "total", perf_total, aba=aba, visao=visao)
            return response

        if aba == 'inativos':
            perf_step = time.perf_counter()
            response = render_aba_inativos(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                apenas_meus=apenas_meus,
                total_oracle_badge=total_oracle_badge,
                total_ativos_badge=total_ativos_badge,
                total_inativos_badge=total_inativos_badge,
                total_proximos_badge=total_proximos_badge,
                cache_store=_INATIVOS_COUNT_CACHE,
                total_construtoras_badge=total_construtoras_badge,
                total_retornos_atrasados_badge=total_retornos_atrasados_badge,
                dashboard_tipo=dashboard_tipo,
                visao=visao,
                agrupar_por=agrupar_por,
                periodo_recencia="ultimos_3_anos",
            )
            _log_perf(app, "render_aba_inativos", perf_step, visao=visao)
            _log_perf(app, "total", perf_total, aba=aba, visao=visao)
            return response

        if aba == 'construtoras':
            perf_step = time.perf_counter()
            response = render_aba_construtoras(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                apenas_meus=apenas_meus,
                total_oracle_badge=total_oracle_badge,
                total_ativos_badge=total_ativos_badge,
                total_inativos_badge=total_inativos_badge,
                total_proximos_badge=total_proximos_badge,
                total_construtoras_badge=total_construtoras_badge,
                total_retornos_atrasados_badge=total_retornos_atrasados_badge,
                dashboard_tipo=dashboard_tipo,
                visao=visao,
                agrupar_por=agrupar_por,
                periodo_recencia=periodo_recencia,
            )
            _log_perf(app, "render_aba_construtoras", perf_step, visao=visao)
            _log_perf(app, "total", perf_total, aba=aba, visao=visao)
            return response

        if aba == 'proximos_inativacao':
            perf_step = time.perf_counter()
            response = render_aba_proximos_inativacao(
                aba=aba,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                total_oracle_badge=total_oracle_badge,
                total_ativos_badge=total_ativos_badge,
                total_inativos_badge=total_inativos_badge,
                total_proximos_badge=total_proximos_badge,
                total_construtoras_badge=total_construtoras_badge,
                total_retornos_atrasados_badge=total_retornos_atrasados_badge,
                q=request.args.get('q', ''),
                dashboard_tipo=dashboard_tipo,
                visao=visao,
                agrupar_por=agrupar_por,
                periodo_recencia=periodo_recencia,
            )
            _log_perf(app, "render_aba_proximos", perf_step, visao=visao)
            _log_perf(app, "total", perf_total, aba=aba, visao=visao)
            return response

        perf_step = time.perf_counter()
        response = render_fluxo_operacional(
            request=request,
            current_user=current_user,
            aba=aba,
            total_oracle_badge=total_oracle_badge,
            total_ativos_badge=total_ativos_badge,
            total_proximos_badge=total_proximos_badge,
            apenas_meus=apenas_meus,
            codigos_representantes_vinculados=codigos_representantes_vinculados,
            cache_store=_INATIVOS_COUNT_CACHE,
            cache_ttl_seconds=_INATIVOS_COUNT_CACHE_TTL_SECONDS,
            total_construtoras_badge=total_construtoras_badge,
            dashboard_tipo=dashboard_tipo,
            visao=visao,
            agrupar_por=agrupar_por,
            periodo_recencia=periodo_recencia,
        )
        _log_perf(app, "render_fluxo_operacional", perf_step, aba=aba, visao=visao)
        _log_perf(app, "total", perf_total, aba=aba, visao=visao)
        return response

    @app.route('/api/clientes/badges')
    def api_clientes_badges():
        contexto_inicial = preparar_contexto_inicial_listagem(request, current_user)
        if contexto_inicial.get("response") is not None:
            return jsonify({"ok": False, "erro": "nao_autorizado"}), 403

        aba = contexto_inicial["aba"]
        total_oracle_badge = int(contexto_inicial["total_oracle_badge"] or 0)
        total_proximos_badge = int(contexto_inicial["total_proximos_badge"] or 0)
        apenas_meus = contexto_inicial["apenas_meus"]
        codigos_representantes_vinculados = contexto_inicial["codigos_representantes_vinculados"]
        total_inativos_badge = int(
            calcular_total_inativos_badge_com_cache(
                current_user=current_user,
                apenas_meus=apenas_meus,
                cache_store=_INATIVOS_COUNT_CACHE,
                cache_ttl_seconds=_INATIVOS_COUNT_CACHE_TTL_SECONDS,
            )
            or 0
        )
        total_pendentes_badge, total_retornar_badge = _calcular_badges_operacionais(
            aba=aba,
            apenas_meus=apenas_meus,
            codigos_representantes_vinculados=codigos_representantes_vinculados,
            dashboard_tipo=contexto_inicial["dashboard_tipo"],
        )

        return jsonify(
            {
                "ok": True,
                "badges": {
                    "pendentes": int(total_pendentes_badge),
                    "retornar": int(total_retornar_badge),
                    "oracle": total_oracle_badge,
                    "ativos": int(contexto_inicial.get("total_ativos_badge", 0)),
                    "inativos": total_inativos_badge,
                    "proximos_inativacao": total_proximos_badge,
                    "construtoras": int(contexto_inicial.get("total_construtoras_badge", 0)),
                },
            }
        )

    @app.route('/api/clientes/grupo-html')
    def api_clientes_grupo_html():
        contexto_inicial = preparar_contexto_inicial_listagem(request, current_user)
        if contexto_inicial.get("response") is not None:
            return jsonify({"ok": False, "erro": "nao_autorizado"}), 403

        aba = contexto_inicial["aba"]
        grupo = (request.args.get("grupo") or "").strip()
        offset = request.args.get("offset") or 0
        limit = request.args.get("limit") or 150
        agrupar_por = _resolver_agrupar_por(aba)
        periodo_recencia = _resolver_periodo_recencia(aba)

        if aba == "pendentes":
            html = render_grupo_operacional_agrupado_html(
                request_args=request.args,
                current_user=current_user,
                aba=aba,
                apenas_meus=contexto_inicial["apenas_meus"],
                codigos_representantes_vinculados=contexto_inicial["codigos_representantes_vinculados"],
                dashboard_tipo=contexto_inicial["dashboard_tipo"],
                agrupar_por=agrupar_por,
                periodo_recencia=periodo_recencia,
            )
        elif aba == "oracle":
            html = render_aba_oracle(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=contexto_inicial["codigos_representantes_vinculados"],
                apenas_meus=contexto_inicial["apenas_meus"],
                total_oracle_badge=contexto_inicial["total_oracle_badge"],
                total_ativos_badge=contexto_inicial.get("total_ativos_badge", 0),
                total_inativos_badge=0,
                total_proximos_badge=contexto_inicial["total_proximos_badge"],
                total_construtoras_badge=contexto_inicial.get("total_construtoras_badge", 0),
                dashboard_tipo=contexto_inicial["dashboard_tipo"],
                visao=contexto_inicial["visao"],
                agrupar_por=agrupar_por,
                periodo_recencia=periodo_recencia,
                lazy_grupo_nome=grupo,
                lazy_offset=offset,
                lazy_limit=limit,
            )
        elif aba == "ativos":
            html = render_aba_ativos(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=contexto_inicial["codigos_representantes_vinculados"],
                apenas_meus=contexto_inicial["apenas_meus"],
                total_oracle_badge=contexto_inicial["total_oracle_badge"],
                total_ativos_badge=contexto_inicial.get("total_ativos_badge", 0),
                total_inativos_badge=0,
                total_proximos_badge=contexto_inicial["total_proximos_badge"],
                total_construtoras_badge=contexto_inicial.get("total_construtoras_badge", 0),
                dashboard_tipo=contexto_inicial["dashboard_tipo"],
                visao=contexto_inicial["visao"],
                agrupar_por=agrupar_por,
                periodo_recencia=periodo_recencia,
                lazy_grupo_nome=grupo,
                lazy_offset=offset,
                lazy_limit=limit,
            )
        elif aba == "inativos":
            html = render_aba_inativos(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=contexto_inicial["codigos_representantes_vinculados"],
                apenas_meus=contexto_inicial["apenas_meus"],
                total_oracle_badge=contexto_inicial["total_oracle_badge"],
                total_ativos_badge=contexto_inicial.get("total_ativos_badge", 0),
                total_inativos_badge=0,
                total_proximos_badge=contexto_inicial["total_proximos_badge"],
                cache_store=_INATIVOS_COUNT_CACHE,
                total_construtoras_badge=contexto_inicial.get("total_construtoras_badge", 0),
                dashboard_tipo=contexto_inicial["dashboard_tipo"],
                visao=contexto_inicial["visao"],
                agrupar_por=agrupar_por,
                periodo_recencia="ultimos_3_anos",
                lazy_grupo_nome=grupo,
                lazy_offset=offset,
                lazy_limit=limit,
            )
        elif aba == "construtoras":
            html = render_aba_construtoras(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=contexto_inicial["codigos_representantes_vinculados"],
                apenas_meus=contexto_inicial["apenas_meus"],
                total_oracle_badge=contexto_inicial["total_oracle_badge"],
                total_ativos_badge=contexto_inicial.get("total_ativos_badge", 0),
                total_inativos_badge=0,
                total_proximos_badge=contexto_inicial["total_proximos_badge"],
                total_construtoras_badge=contexto_inicial.get("total_construtoras_badge", 0),
                dashboard_tipo=contexto_inicial["dashboard_tipo"],
                visao=contexto_inicial["visao"],
                agrupar_por=agrupar_por,
                periodo_recencia=periodo_recencia,
                lazy_grupo_nome=grupo,
                lazy_offset=offset,
                lazy_limit=limit,
            )
        elif aba == "proximos_inativacao":
            html = render_aba_proximos_inativacao(
                aba=aba,
                current_user=current_user,
                codigos_representantes_vinculados=contexto_inicial["codigos_representantes_vinculados"],
                total_oracle_badge=contexto_inicial["total_oracle_badge"],
                total_ativos_badge=contexto_inicial.get("total_ativos_badge", 0),
                total_inativos_badge=0,
                total_proximos_badge=contexto_inicial["total_proximos_badge"],
                total_construtoras_badge=contexto_inicial.get("total_construtoras_badge", 0),
                q=request.args.get('q', ''),
                dashboard_tipo=contexto_inicial["dashboard_tipo"],
                visao=contexto_inicial["visao"],
                agrupar_por=agrupar_por,
                periodo_recencia=periodo_recencia,
                lazy_grupo_nome=grupo,
                lazy_offset=offset,
                lazy_limit=limit,
            )
        else:
            html = None
        if html is None:
            return jsonify({"ok": False, "erro": "grupo_invalido"}), 400
        return html

    @app.route('/api/clientes/tabela-html')
    def api_clientes_tabela_html():
        contexto_inicial = preparar_contexto_inicial_listagem(request, current_user)
        if contexto_inicial.get("response") is not None:
            return jsonify({"ok": False, "erro": "nao_autorizado"}), 403

        aba = contexto_inicial["aba"]
        if aba not in ("pendentes", "contatados", "retornar"):
            return jsonify({"ok": False, "erro": "aba_sem_tabela"}), 400

        return render_tabela_operacional_html(
            request_args=request.args,
            current_user=current_user,
            aba=aba,
            apenas_meus=contexto_inicial["apenas_meus"],
            codigos_representantes_vinculados=contexto_inicial["codigos_representantes_vinculados"],
            dashboard_tipo=contexto_inicial["dashboard_tipo"],
        )
