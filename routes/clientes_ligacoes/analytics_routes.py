from flask import jsonify, request
from flask_login import current_user, login_required

from routes.clientes_ligacoes.analytics_api import (
    consultar_ligacoes_consultor_mes,
    consultar_resultados_consultores_mes,
    parse_mes_ano,
)


def register_clientes_ligacoes_analytics_routes(app):
    @app.route("/api/resultados-por-mes")
    @login_required
    def api_resultados_por_mes():
        if current_user.tipo != "supervisor":
            return jsonify({"erro": "Acesso negado"}), 403

        try:
            mes, ano = parse_mes_ano(request.args)
            payload, status = consultar_resultados_consultores_mes(mes, ano)
            return jsonify(payload), status
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    @app.route("/api/minhas-ligacoes-por-mes")
    @login_required
    def api_minhas_ligacoes_por_mes():
        if current_user.tipo not in ("consultor", "televendas"):
            return jsonify({"erro": "Acesso negado"}), 403

        try:
            mes, ano = parse_mes_ano(request.args)
            return jsonify(consultar_ligacoes_consultor_mes(current_user.id, mes, ano))
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500
