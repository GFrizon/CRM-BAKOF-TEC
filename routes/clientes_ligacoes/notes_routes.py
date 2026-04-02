from flask import jsonify, request
from flask_login import current_user

from core.helpers import s
from routes.clientes_ligacoes.access_control import resposta_supervisor_repr_somente_leitura
from routes.clientes_ligacoes.interaction_serializers import serializar_notas
from routes.clientes_ligacoes.notes_service import (
    adicionar_nota_service,
    listar_notas_service,
)


def register_clientes_ligacoes_notes_routes(app):
    @app.route("/clientes/<int:cliente_id>/notas", methods=["GET"])
    def listar_notas(cliente_id: int):
        if not current_user.is_authenticated:
            return jsonify([])
        notas = listar_notas_service(cliente_id)
        return jsonify(serializar_notas(notas))

    @app.route("/clientes/<int:cliente_id>/notas", methods=["POST"])
    def adicionar_nota(cliente_id: int):
        if not current_user.is_authenticated:
            return jsonify({"ok": False, "mensagem": "Não autenticado"}), 401

        if current_user.tipo == "supervisor_repr":
            return resposta_supervisor_repr_somente_leitura(
                "Usuários do tipo Supervisor de Representante não podem adicionar notas (somente visualização)."
            )

        texto = s((request.get_json(silent=True) or {}).get("texto"))
        if not texto:
            return jsonify({"ok": False, "mensagem": "Texto obrigatório"}), 400

        resposta, status = adicionar_nota_service(cliente_id, current_user, texto)
        return jsonify(resposta), status
