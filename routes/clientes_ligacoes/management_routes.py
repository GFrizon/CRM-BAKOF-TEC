from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from core.extensions import db
from core.helpers import get_pos, s, so_digits
from core.models import Usuario
from routes.clientes_ligacoes.access_control import resposta_supervisor_repr_somente_leitura
from routes.clientes_ligacoes.cache_invalidation import invalidar_caches_listagens_clientes
from routes.clientes_ligacoes.call_record_service import registrar_ligacao_service
from routes.clientes_ligacoes.client_manual_service import criar_ou_atualizar_cliente_manual
from routes.clientes_ligacoes.contact_service import iniciar_contato_service
from routes.clientes_ligacoes.import_flow import executar_importacao_completa
from routes.clientes_ligacoes.import_helpers import carregar_dataframe_importacao
from routes.clientes_ligacoes.lock_helpers import (
    buscar_locks_por_cd_oracle,
    extrair_cds_da_requisicao,
)
from routes.clientes_ligacoes.maintenance_helpers import inativar_clientes_do_consultor
from routes.clientes_ligacoes.oracle_prefill_service import (
    buscar_dados_oracle_para_preenchimento,
)
from routes.clientes_ligacoes.oracle_sync_service import (
    sincronizar_clientes_manuais_oracle_service,
    sincronizar_cliente_oracle_por_id_service,
)


def register_clientes_ligacoes_management_routes(app):
    @app.route("/clientes/preencher-oracle-cnpj", methods=["POST"])
    @login_required
    def preencher_cliente_oracle_por_cnpj():
        try:
            payload = request.get_json(silent=True) or {}
            resposta, status = buscar_dados_oracle_para_preenchimento(payload.get("cnpj"))
            return jsonify(resposta), status
        except Exception as e:
            return jsonify({"ok": False, "mensagem": f"Erro ao buscar no Oracle: {str(e)}"}), 500

    @app.route("/clientes/<int:cliente_id>/sincronizar-oracle", methods=["POST"])
    @login_required
    def sincronizar_cliente_oracle_por_id(cliente_id: int):
        try:
            if current_user.tipo != "supervisor":
                return jsonify({"ok": False, "mensagem": "Acesso permitido apenas para supervisores"}), 403

            payload = request.get_json(silent=True) or {}
            resposta, status = sincronizar_cliente_oracle_por_id_service(cliente_id, payload)
            return jsonify(resposta), status
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro ao sincronizar cliente com Oracle: {str(e)}"}), 500

    @app.route("/clientes/sincronizar-manuais-oracle", methods=["POST"])
    @login_required
    def sincronizar_clientes_manuais_oracle():
        try:
            if current_user.tipo != "supervisor":
                return jsonify({"ok": False, "mensagem": "Acesso permitido apenas para supervisores"}), 403
            resposta, status = sincronizar_clientes_manuais_oracle_service()
            return jsonify(resposta), status
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro no sync manual com Oracle: {str(e)}"}), 500

    @app.route("/clientes/criar", methods=["POST"])
    @login_required
    def criar_cliente_manual():
        try:
            if current_user.tipo == "supervisor_repr":
                return resposta_supervisor_repr_somente_leitura(
                    "Usuarios do tipo Supervisor de Representante nao podem criar clientes (somente visualizacao)."
                )

            payload = request.get_json(silent=True) or {}
            resposta, status = criar_ou_atualizar_cliente_manual(payload, current_user)
            return jsonify(resposta), status

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/api/clientes/<int:cliente_id>/iniciar-contato", methods=["POST"])
    @login_required
    def iniciar_contato_cliente(cliente_id: int):
        try:
            if current_user.tipo == "supervisor_repr":
                return resposta_supervisor_repr_somente_leitura(
                    "Usuarios do tipo Supervisor de Representante nao podem iniciar contato (somente visualizacao)."
                )

            payload = request.get_json(silent=True) or {}
            resposta, status = iniciar_contato_service(current_user, cliente_id, payload)
            return jsonify(resposta), status
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/api/inativos/locks", methods=["GET", "POST"])
    @login_required
    def listar_locks_inativos():
        try:
            if current_user.tipo not in ("televendas", "supervisor"):
                return jsonify({"ok": False, "mensagem": "Sem permissao"}), 403

            cds = extrair_cds_da_requisicao(request)
            if not cds:
                return jsonify({"ok": True, "locks": {}})

            locks = buscar_locks_por_cd_oracle(cds)
            return jsonify({"ok": True, "locks": locks})
        except Exception as e:
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/registrar-ligacao/<int:cliente_id>", methods=["POST"])
    def registrar_ligacao(cliente_id: int):
        if not current_user.is_authenticated:
            return jsonify({"ok": False, "mensagem": "Nao autenticado"}), 401

        if current_user.tipo == "supervisor_repr":
            return resposta_supervisor_repr_somente_leitura(
                "Usuarios do tipo Supervisor de Representante nao podem registrar ligacoes (somente visualizacao)."
            )

        try:
            payload = request.get_json(silent=True) or {}
            resposta, status = registrar_ligacao_service(current_user, cliente_id, payload)
            return jsonify(resposta), status
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/importar-clientes", methods=["GET", "POST"])
    def importar_clientes_view():
        if not current_user.is_authenticated:
            return redirect(url_for("login"))

        if current_user.tipo != "supervisor":
            flash("Acesso permitido somente para supervisores.", "danger")
            return redirect(url_for("meus_clientes"))

        if request.method == "POST":
            consultor_id = request.form.get("consultor_id")
            arquivo = request.files.get("arquivo")

            if not consultor_id or not arquivo:
                flash("Selecione o consultor e o arquivo (.xlsx ou .csv).", "warning")
                return redirect(url_for("importar_clientes_view"))

            consultor_id = int(consultor_id)
            filename = getattr(arquivo, "filename", "") or ""
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

            df = carregar_dataframe_importacao(arquivo, ext)
            resultado_import = executar_importacao_completa(
                df=df,
                filename=filename,
                consultor_id=consultor_id,
                logger=app.logger,
                get_pos_fn=get_pos,
                normalizar_texto_fn=s,
                so_digits_fn=so_digits,
            )
            if not resultado_import.get("ok"):
                flash("Erro ao salvar dados no banco. Nenhum dado foi importado.", "danger")
                return redirect(url_for("importar_clientes_view"))

            total_inseridos = int(resultado_import.get("total_inseridos") or 0)
            pulados = int(resultado_import.get("pulados") or 0)
            erros = list(resultado_import.get("erros") or [])

            msg = (
                f"Importacao concluida! Inseridos/Atualizados/Reativados: {total_inseridos} - "
                f"Pulados: {pulados}"
            )
            if erros:
                msg += f" - Erros: {len(erros)} (mostrando ate 50)"
            flash(msg, "success")
            for erro in erros[:50]:
                flash(erro, "warning")

            invalidar_caches_listagens_clientes("importacao de clientes")
            return redirect(url_for("meus_clientes"))

        consultores = Usuario.query.filter_by(tipo="consultor", ativo=True).order_by(Usuario.nome.asc()).all()
        return render_template("importar.html", consultores=consultores)

    @app.route("/limpar-clientes-consultor", methods=["POST"])
    @login_required
    def limpar_clientes_consultor():
        if not current_user.is_authenticated:
            return jsonify({"ok": False, "mensagem": "Nao autenticado"}), 401

        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            payload = request.get_json(silent=True) or {}
            consultor_id = payload.get("consultor_id")
            if not consultor_id:
                return jsonify({"ok": False, "mensagem": "Consultor nao informado"}), 400

            total = inativar_clientes_do_consultor(consultor_id)
            db.session.commit()
            invalidar_caches_listagens_clientes("limpeza de carteira de consultor")
            return jsonify({"ok": True, "mensagem": f"{total} clientes removidos com sucesso."})
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500
