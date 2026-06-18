from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from core.extensions import db
from core.helpers import get_pos, s, so_digits
from core.models import CnpjRaizOcultoInativos, Usuario
from routes.clientes_ligacoes.access_control import (
    resposta_representante_somente_leitura,
    resposta_supervisor_dev_obrigatorio,
    resposta_supervisor_repr_somente_leitura,
    supervisor_dev_liberado,
)
from routes.clientes_ligacoes.cache_invalidation import invalidar_caches_listagens_clientes
from routes.clientes_ligacoes.call_record_service import (
    localizar_cliente_para_registro,
    registrar_ligacao_service,
)
from routes.clientes_ligacoes.client_manual_service import criar_ou_atualizar_cliente_manual
from routes.clientes_ligacoes.contact_service import iniciar_contato_service
from routes.clientes_ligacoes.import_flow import executar_importacao_completa
from routes.clientes_ligacoes.import_helpers import carregar_dataframe_importacao
from routes.clientes_ligacoes.lock_helpers import (
    buscar_locks_por_cd_oracle,
    extrair_cds_da_requisicao,
)
from routes.clientes_ligacoes.maintenance_helpers import inativar_clientes_do_consultor
from routes.clientes_ligacoes.inativos_cnpj_raiz_filter import (
    normalizar_cnpjs_raiz_para_lista,
)
from routes.clientes_ligacoes.oracle_prefill_service import (
    buscar_dados_oracle_para_preenchimento,
)
from routes.clientes_ligacoes.oracle_sync_service import (
    sincronizar_clientes_manuais_oracle_service,
    sincronizar_cliente_oracle_por_id_service,
)


def register_clientes_ligacoes_management_routes(app):
    def _resolver_retorno_inativos():
        retorno = str(request.values.get("return_to") or "").strip()
        if retorno.startswith("/meus-clientes"):
            return retorno

        valores = {
            "aba": "inativos",
            "visao": request.values.get("visao") or request.args.get("visao") or "clientes",
        }
        dashboard_tipo = request.values.get("dashboard_tipo") or request.args.get("dashboard_tipo")
        agrupar_por = request.values.get("agrupar_por") or request.args.get("agrupar_por")
        conceito_filtro = request.values.get("conceito_filtro") or request.args.get("conceito_filtro")
        consultor_filtro = request.values.get("consultor_filtro") or request.args.get("consultor_filtro")
        if dashboard_tipo:
            valores["dashboard_tipo"] = dashboard_tipo
        if agrupar_por:
            valores["agrupar_por"] = agrupar_por
        if conceito_filtro:
            valores["conceito_filtro"] = conceito_filtro
        if consultor_filtro:
            valores["consultor_filtro"] = consultor_filtro
        return url_for("meus_clientes", **valores)

    @app.route("/meus-clientes/inativos/cnpj-raiz-ocultos", methods=["GET", "POST"])
    @login_required
    def configurar_cnpjs_raiz_ocultos_inativos():
        if current_user.tipo != "supervisor":
            flash("Acesso permitido somente para supervisores.", "danger")
            return redirect(url_for("meus_clientes", aba="inativos"))

        return_to = _resolver_retorno_inativos()
        ativos = (
            CnpjRaizOcultoInativos.query
            .filter_by(ativo=True)
            .order_by(CnpjRaizOcultoInativos.cnpj_raiz.asc())
            .all()
        )

        if request.method == "POST":
            try:
                bruto = str(request.form.get("cnpjs_raiz") or "")
                linhas = [linha.strip() for linha in bruto.replace("\r", "").split("\n")]
                cnpjs_raiz = normalizar_cnpjs_raiz_para_lista(linhas)
                invalidos = [
                    linha for linha in linhas
                    if linha and len("".join(ch for ch in linha if ch.isdigit())) < 8
                ]

                existentes = {
                    str(item.cnpj_raiz or "").strip(): item
                    for item in CnpjRaizOcultoInativos.query.all()
                }
                novas_raizes = set(cnpjs_raiz)

                for raiz, item in existentes.items():
                    item.ativo = raiz in novas_raizes
                    item.atualizado_por_id = current_user.id

                for raiz in cnpjs_raiz:
                    if raiz in existentes:
                        continue
                    db.session.add(
                        CnpjRaizOcultoInativos(
                            cnpj_raiz=raiz,
                            ativo=True,
                            atualizado_por_id=current_user.id,
                        )
                    )

                db.session.commit()
                invalidar_caches_listagens_clientes("configuracao cnpj raiz oculto inativos")

                if invalidos:
                    flash(
                        f"Configuracao salva. {len(invalidos)} linha(s) foram ignoradas por nao terem 8 digitos validos.",
                        "warning",
                    )
                else:
                    flash("Configuracao salva com sucesso.", "success")
            except Exception as e:
                db.session.rollback()
                flash(f"Erro ao salvar configuracao: {e}", "danger")

            return redirect(
                url_for(
                    "configurar_cnpjs_raiz_ocultos_inativos",
                    return_to=return_to,
                    visao=request.form.get("visao") or request.args.get("visao") or "clientes",
                    dashboard_tipo=request.form.get("dashboard_tipo") or request.args.get("dashboard_tipo") or "",
                    agrupar_por=request.form.get("agrupar_por") or request.args.get("agrupar_por") or "",
                    conceito_filtro=request.form.get("conceito_filtro") or request.args.get("conceito_filtro") or "",
                    consultor_filtro=request.form.get("consultor_filtro") or request.args.get("consultor_filtro") or "",
                )
            )

        cnpjs_raiz_texto = "\n".join(
            str(item.cnpj_raiz or "").strip()
            for item in ativos
            if str(item.cnpj_raiz or "").strip()
        )
        return render_template(
            "meus_clientes/inativos_cnpj_raiz_ocultos.html",
            cnpjs_raiz_texto=cnpjs_raiz_texto,
            cnpjs_raiz_ocultos=ativos,
            return_to=return_to,
        )

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
            if not supervisor_dev_liberado():
                return resposta_supervisor_dev_obrigatorio()

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
            if not supervisor_dev_liberado():
                return resposta_supervisor_dev_obrigatorio()
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
            if current_user.tipo == "representante":
                return resposta_representante_somente_leitura(
                    "Usuarios do tipo Representante nao podem criar clientes (somente visualizacao)."
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
            if current_user.tipo == "representante":
                return resposta_representante_somente_leitura(
                    "Usuarios do tipo Representante nao podem iniciar contato (somente visualizacao)."
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
            if current_user.tipo not in ("consultor", "televendas", "supervisor", "representante"):
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
        if current_user.tipo == "representante":
            return resposta_representante_somente_leitura(
                "Usuarios do tipo Representante nao podem registrar ligacoes (somente visualizacao)."
            )

        try:
            payload = request.get_json(silent=True) or {}
            resposta, status = registrar_ligacao_service(current_user, cliente_id, payload)
            return jsonify(resposta), status
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/registrar-venda-retroativa", methods=["POST"])
    def registrar_venda_retroativa():
        if not current_user.is_authenticated:
            return jsonify({"ok": False, "mensagem": "Nao autenticado"}), 401

        if current_user.tipo == "supervisor_repr":
            return resposta_supervisor_repr_somente_leitura(
                "Usuarios do tipo Supervisor de Representante nao podem registrar ligacoes (somente visualizacao)."
            )
        if current_user.tipo == "representante":
            return resposta_representante_somente_leitura(
                "Usuarios do tipo Representante nao podem registrar ligacoes (somente visualizacao)."
            )

        try:
            payload = request.get_json(silent=True) or {}
            cd_cliente_oracle = so_digits(payload.get("cd_cliente_oracle"))
            cnpj = so_digits(payload.get("cnpj"))
            contato_nome = s(payload.get("contato_nome"))

            if not cd_cliente_oracle and not cnpj:
                return jsonify(
                    {
                        "ok": False,
                        "mensagem": "Informe o Codigo Oracle ou o CNPJ para localizar o cliente.",
                    }
                ), 400

            cliente = localizar_cliente_para_registro(cd_cliente_oracle, cnpj)
            if not cliente:
                return jsonify(
                    {
                        "ok": False,
                        "mensagem": "Cliente nao encontrado para o Codigo/CNPJ informado.",
                    }
                ), 404

            payload_ligacao = {
                "contato_nome": contato_nome or "Registro retroativo",
                "resultado": "comprou",
                "valor_venda": payload.get("valor_venda"),
                "observacao": s(payload.get("observacao")),
            }

            resposta, status = registrar_ligacao_service(current_user, cliente.id, payload_ligacao)
            if status == 200:
                resposta["cliente_id"] = cliente.id
                resposta["cliente_nome"] = cliente.nome
                resposta["cd_cliente_oracle"] = cliente.cd_cliente_oracle
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
        if not supervisor_dev_liberado():
            flash("Acesso permitido apenas no modo dev do supervisor.", "warning")
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
        if not supervisor_dev_liberado():
            return resposta_supervisor_dev_obrigatorio()

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


    @app.route("/api/clientes/<int:cliente_id>/editar", methods=["POST"])
    @login_required
    def editar_cliente_manual(cliente_id):
        if not current_user.is_authenticated:
            return jsonify({"sucesso": False, "mensagem": "Nao autenticado"}), 401

        if current_user.tipo in ("supervisor_repr", "representante"):
            return jsonify({"sucesso": False, "mensagem": "Acesso negado"}), 403

        from core.models import Cliente

        cliente = Cliente.query.get(cliente_id)
        if not cliente:
            return jsonify({"sucesso": False, "mensagem": "Cliente nao encontrado"}), 404

        if cliente.origem != "manual":
            return jsonify({"sucesso": False, "mensagem": "Apenas clientes manuais podem ser editados"}), 403

        payload = request.get_json(silent=True) or {}
        nome = (payload.get("nome") or "").strip()
        cnpj = (payload.get("cnpj") or "").strip()
        telefone = (payload.get("telefone") or "").strip()
        telefone2 = (payload.get("telefone2") or "").strip()

        if not nome or not telefone:
            return jsonify({"sucesso": False, "mensagem": "Nome e telefone sao obrigatorios"}), 400

        try:
            cliente.nome = nome
            cliente.cnpj = cnpj if cnpj else None
            cliente.telefone = telefone
            cliente.telefone2 = telefone2 if telefone2 else None
            db.session.commit()
            invalidar_caches_listagens_clientes("edicao de cliente manual")
            return jsonify({"sucesso": True, "mensagem": "Cliente atualizado com sucesso"})
        except Exception as e:
            db.session.rollback()
            return jsonify({"sucesso": False, "mensagem": f"Erro ao atualizar: {str(e)}"}), 500
