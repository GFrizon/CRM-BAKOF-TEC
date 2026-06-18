from flask import jsonify, request
from flask_login import current_user, login_required

from core.extensions import db
from core.helpers import formatar_dinheiro, s
from routes.clientes_ligacoes.access_control import (
    resposta_representante_somente_leitura,
    resposta_supervisor_repr_somente_leitura,
)
from routes.clientes_ligacoes.interactions_service import (
    detalhes_ligacao_service,
    editar_ligacao_service,
    editar_observacao_ligacao_service,
    historico_ligacoes_service,
)


def register_clientes_ligacoes_interactions_routes(app):
    @app.route("/editar-observacao/<int:ligacao_id>", methods=["POST"])
    @login_required
    def editar_observacao(ligacao_id: int):
        try:
            if current_user.tipo == "supervisor_repr":
                return resposta_supervisor_repr_somente_leitura(
                    "Usuarios do tipo Supervisor de Representante nao podem editar observacoes (somente visualizacao)."
                )
            if current_user.tipo == "representante":
                return resposta_representante_somente_leitura(
                    "Usuarios do tipo Representante nao podem editar observacoes (somente visualizacao)."
                )

            payload = request.get_json(silent=True) or {}
            resposta, status = editar_observacao_ligacao_service(
                ligacao_id=ligacao_id,
                current_user=current_user,
                observacao=payload.get("observacao"),
                normalizador_texto=s,
            )
            return jsonify(resposta), status
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/editar-ligacao/<int:ligacao_id>", methods=["POST"])
    @login_required
    def editar_ligacao(ligacao_id: int):
        try:
            if current_user.tipo == "supervisor_repr":
                return resposta_supervisor_repr_somente_leitura(
                    "Usuarios do tipo Supervisor de Representante nao podem editar ligacoes (somente visualizacao)."
                )
            if current_user.tipo == "representante":
                return resposta_representante_somente_leitura(
                    "Usuarios do tipo Representante nao podem editar ligacoes (somente visualizacao)."
                )

            payload = request.get_json(silent=True) or {}
            resposta, status = editar_ligacao_service(
                ligacao_id=ligacao_id,
                current_user=current_user,
                payload=payload,
                normalizador_texto=s,
            )
            return jsonify(resposta), status
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/api/detalhes-ligacao/<int:ligacao_id>")
    @login_required
    def api_detalhes_ligacao(ligacao_id: int):
        try:
            resposta, status = detalhes_ligacao_service(ligacao_id, current_user, formatar_dinheiro)
            return jsonify(resposta), status
        except Exception as e:
            return jsonify({"erro": f"Erro: {str(e)}"}), 500

    @app.route("/historico-ligacoes/<int:cliente_id>")
    def historico_ligacoes(cliente_id: int):
        if not current_user.is_authenticated:
            return jsonify([])

        try:
            return jsonify(
                historico_ligacoes_service(
                    cliente_id=cliente_id,
                    current_user=current_user,
                    normalizador_texto=s,
                    formatar_dinheiro_fn=formatar_dinheiro,
                )
            )
        except Exception:
            return jsonify([])
