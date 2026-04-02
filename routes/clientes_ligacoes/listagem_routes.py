from flask import request
from flask_login import current_user

from routes.clientes_ligacoes.listagem_access import preparar_contexto_inicial_listagem
from routes.clientes_ligacoes.listagem_inativos import render_aba_inativos
from routes.clientes_ligacoes.listagem_operacional import render_fluxo_operacional
from routes.clientes_ligacoes.listagem_oracle import render_aba_oracle
from routes.clientes_ligacoes.listagem_proximos import render_aba_proximos_inativacao

_INATIVOS_COUNT_CACHE = {}
_INATIVOS_COUNT_CACHE_TTL_SECONDS = 600


def register_clientes_ligacoes_listagem_routes(app):
    @app.route('/meus-clientes')
    def meus_clientes():
        contexto_inicial = preparar_contexto_inicial_listagem(request, current_user)
        if contexto_inicial.get("response") is not None:
            return contexto_inicial["response"]
        aba = contexto_inicial["aba"]
        total_oracle_badge = contexto_inicial["total_oracle_badge"]
        total_proximos_badge = contexto_inicial["total_proximos_badge"]
        apenas_meus = contexto_inicial["apenas_meus"]
        codigos_representantes_vinculados = contexto_inicial["codigos_representantes_vinculados"]

        if aba == 'oracle':
            return render_aba_oracle(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                apenas_meus=apenas_meus,
                total_proximos_badge=total_proximos_badge,
            )

        if aba == 'inativos':
            return render_aba_inativos(
                app=app,
                aba=aba,
                request=request,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                total_oracle_badge=total_oracle_badge,
                total_proximos_badge=total_proximos_badge,
                cache_store=_INATIVOS_COUNT_CACHE,
            )

        if aba == 'proximos_inativacao':
            return render_aba_proximos_inativacao(
                aba=aba,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                total_oracle_badge=total_oracle_badge,
                q=request.args.get('q', ''),
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
