from flask import jsonify, request
from flask_login import current_user
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from core.extensions import db
from core.models import Cliente, Ligacao
from routes.clientes_ligacoes.badges import calcular_total_inativos_badge_com_cache
from routes.clientes_ligacoes.listagem_access import preparar_contexto_inicial_listagem
from routes.clientes_ligacoes.listagem_inativos import render_aba_inativos
from routes.clientes_ligacoes.listagem_operacional import render_fluxo_operacional
from routes.clientes_ligacoes.listagem_operacional_classificacao import classificar_listas_operacionais
from routes.clientes_ligacoes.listagem_oracle import render_aba_oracle
from routes.clientes_ligacoes.listagem_proximos import render_aba_proximos_inativacao

_INATIVOS_COUNT_CACHE = {}
_INATIVOS_COUNT_CACHE_TTL_SECONDS = 600


def register_clientes_ligacoes_listagem_routes(app):
    def _calcular_badges_operacionais(aba, apenas_meus, codigos_representantes_vinculados):
        q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(Cliente.ativo == True)
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

        clientes_todos = q.order_by(Cliente.nome.asc()).all()
        pendentes, contatados, precisa_retornar = classificar_listas_operacionais(
            clientes_todos=clientes_todos,
            current_user=current_user,
            aba=aba,
            codigos_representantes_vinculados=codigos_representantes_vinculados,
        )
        return len(pendentes), len(contatados), len(precisa_retornar)

    @app.route('/meus-clientes')
    def meus_clientes():
        contexto_inicial = preparar_contexto_inicial_listagem(request, current_user)
        if contexto_inicial.get("response") is not None:
            return contexto_inicial["response"]
        aba = contexto_inicial["aba"]
        dashboard_tipo = contexto_inicial["dashboard_tipo"]
        total_oracle_badge = contexto_inicial["total_oracle_badge"]
        total_proximos_badge = contexto_inicial["total_proximos_badge"]
        apenas_meus = contexto_inicial["apenas_meus"]
        codigos_representantes_vinculados = contexto_inicial["codigos_representantes_vinculados"]
        total_inativos_badge = calcular_total_inativos_badge_com_cache(
            current_user=current_user,
            apenas_meus=apenas_meus,
            cache_store=_INATIVOS_COUNT_CACHE,
            cache_ttl_seconds=_INATIVOS_COUNT_CACHE_TTL_SECONDS,
        )

        if aba == 'oracle':
            return render_aba_oracle(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                apenas_meus=apenas_meus,
                total_inativos_badge=total_inativos_badge,
                total_proximos_badge=total_proximos_badge,
                dashboard_tipo=dashboard_tipo,
            )

        if aba == 'inativos':
            return render_aba_inativos(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                apenas_meus=apenas_meus,
                total_oracle_badge=total_oracle_badge,
                total_inativos_badge=total_inativos_badge,
                total_proximos_badge=total_proximos_badge,
                cache_store=_INATIVOS_COUNT_CACHE,
                dashboard_tipo=dashboard_tipo,
            )

        if aba == 'proximos_inativacao':
            return render_aba_proximos_inativacao(
                aba=aba,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                total_oracle_badge=total_oracle_badge,
                total_inativos_badge=total_inativos_badge,
                q=request.args.get('q', ''),
                dashboard_tipo=dashboard_tipo,
            )

        return render_fluxo_operacional(
            request=request,
            current_user=current_user,
            aba=aba,
            total_oracle_badge=total_oracle_badge,
            total_proximos_badge=total_proximos_badge,
            apenas_meus=apenas_meus,
            codigos_representantes_vinculados=codigos_representantes_vinculados,
            cache_store=_INATIVOS_COUNT_CACHE,
            cache_ttl_seconds=_INATIVOS_COUNT_CACHE_TTL_SECONDS,
        )

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
        total_pendentes_badge, total_contatados_badge, total_retornar_badge = _calcular_badges_operacionais(
            aba=aba,
            apenas_meus=apenas_meus,
            codigos_representantes_vinculados=codigos_representantes_vinculados,
        )

        return jsonify(
            {
                "ok": True,
                "badges": {
                    "pendentes": int(total_pendentes_badge),
                    "contatados": int(total_contatados_badge),
                    "retornar": int(total_retornar_badge),
                    "oracle": total_oracle_badge,
                    "inativos": total_inativos_badge,
                    "proximos_inativacao": total_proximos_badge,
                },
            }
        )
