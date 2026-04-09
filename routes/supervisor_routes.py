from datetime import datetime, timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, desc, func, or_
from sqlalchemy.orm import joinedload
from werkzeug.security import generate_password_hash

from core.extensions import db
from core.helpers import _percent, formatar_dinheiro, s
from core.models import Banner, Cliente, Ligacao, Usuario, SupervisorRepresentanteVinculo
from routes.clientes_ligacoes.badges import _total_inativos_badge
from routes.clientes_ligacoes.oracle_tab import carregar_clientes_oracle_deduplicados


def _ultimos_meses(qtd=12):
    data_atual = datetime.now()
    meses_nomes = {
        1: "Janeiro",
        2: "Fevereiro",
        3: "Março",
        4: "Abril",
        5: "Maio",
        6: "Junho",
        7: "Julho",
        8: "Agosto",
        9: "Setembro",
        10: "Outubro",
        11: "Novembro",
        12: "Dezembro",
    }

    meses = []
    base = data_atual.year * 12 + (data_atual.month - 1)
    for i in range(qtd):
        atual = base - i
        ano = atual // 12
        mes = (atual % 12) + 1
        meses.append({"mes": mes, "ano": ano, "texto": f"{meses_nomes[mes]}/{ano}"})
    return meses


def get_banners_ativos():
    agora = datetime.now()
    return (
        Banner.query.filter(Banner.ativo == True)
        .filter(or_(Banner.data_expiracao == None, Banner.data_expiracao >= agora))
        .order_by(Banner.data_criacao.desc())
        .all()
    )


def register_supervisor_routes(app):
    def _sincronizar_vinculos_tg650_supervisor_repr(supervisor_id: int, codigo_supervisor_tg650: str):
        codigo_base = s(codigo_supervisor_tg650)
        if not codigo_base:
            return {
                "ok": False,
                "mensagem": "Código TG650 não configurado para este supervisor",
                "novos": 0,
                "atualizados": 0,
            }

        from oracle_service import get_vinculos_supervisor_representante_oracle

        codigos_teste = [codigo_base]
        if codigo_base.isdigit():
            codigo_sem_zero = str(int(codigo_base))
            codigo_3 = codigo_sem_zero.zfill(3)
            for cand in (codigo_sem_zero, codigo_3):
                if cand and cand not in codigos_teste:
                    codigos_teste.append(cand)

        vinculos_oracle = []
        codigo_utilizado = codigo_base
        for codigo_teste in codigos_teste:
            dados = get_vinculos_supervisor_representante_oracle(codigo_teste)
            if dados:
                vinculos_oracle = dados
                codigo_utilizado = codigo_teste
                break

        if not vinculos_oracle:
            return {
                "ok": False,
                "mensagem": "Nenhum vínculo encontrado na TG 650",
                "novos": 0,
                "atualizados": 0,
            }

        novos = 0
        atualizados = 0

        for vinculo_oracle in vinculos_oracle:
            cd_representante = str(vinculo_oracle.get("cd_representante") or "").strip()
            if not cd_representante:
                continue

            nome_representante = vinculo_oracle.get("nome_representante")

            vinculo_local = SupervisorRepresentanteVinculo.query.filter_by(
                supervisor_id=supervisor_id,
                codigo_representante=cd_representante,
            ).first()

            if vinculo_local:
                vinculo_local.nome_representante = nome_representante
                vinculo_local.sincronizado_tg650 = True
                vinculo_local.ativo = True
                vinculo_local.codigo_supervisor_tg650 = codigo_utilizado
                atualizados += 1
            else:
                db.session.add(
                    SupervisorRepresentanteVinculo(
                        supervisor_id=supervisor_id,
                        codigo_representante=cd_representante,
                        nome_representante=nome_representante,
                        ativo=True,
                        sincronizado_tg650=True,
                        codigo_supervisor_tg650=codigo_utilizado,
                    )
                )
                novos += 1

        db.session.commit()
        return {
            "ok": True,
            "mensagem": f"Sincronização concluída! {novos} novos, {atualizados} atualizados.",
            "novos": novos,
            "atualizados": atualizados,
        }

    @app.route("/supervisor/televendas", endpoint="dashboard_supervisor_televendas")
    @app.route("/supervisor", endpoint="dashboard_supervisor")
    @login_required
    def supervisor_dashboard():
        if current_user.tipo != "supervisor":
            return redirect(url_for("meus_clientes"))
        dashboard_tipo = "televendas" if request.path.endswith("/televendas") else "consultor"
        dashboard_titulo = "Televendas" if dashboard_tipo == "televendas" else "Consultores"

        mes_filtro = int(request.args.get("mes", datetime.now().month))
        ano_filtro = int(request.args.get("ano", datetime.now().year))

        hoje = datetime.now().date()
        desde = datetime.now() - timedelta(days=30)

        operadores_ids_query = (
            db.session.query(Usuario.id)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
        )
        filtrar_carteira_por_vinculo = (dashboard_tipo == "consultor")
        total_consultores = Usuario.query.filter_by(tipo=dashboard_tipo, ativo=True).count()
        # Regra solicitada: card "Total de Clientes" sempre global
        # (mesmo valor em Consultores e Televendas).
        total_clientes = Cliente.query.filter(Cliente.ativo == True).count()
        total_ligacoes = (
            db.session.query(func.count(Ligacao.id))
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
            .scalar()
        ) or 0
        ligacoes_hoje = (
            db.session.query(func.count(Ligacao.id))
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(
                Usuario.tipo == dashboard_tipo,
                Usuario.ativo == True,
                func.date(Ligacao.data_hora) == hoje,
            )
            .scalar()
        ) or 0

        agora = datetime.now()
        limite_90 = agora - timedelta(days=90)
        limite_150 = agora - timedelta(days=150)
        limite_151 = agora - timedelta(days=151)
        limite_180 = agora - timedelta(days=180)
        limite_181 = agora - timedelta(days=181)
        limite_730 = agora - timedelta(days=730)

        try:
            # Mesma fonte da aba Oracle para manter o número idêntico ao da lista.
            clientes_oracle = carregar_clientes_oracle_deduplicados(app.logger, periodo_oracle=None)
            total_sem_pedido_90_150 = len(clientes_oracle or [])
        except Exception:
            # Fallback local caso Oracle indisponível.
            total_sem_pedido_90_150_query = (
                Cliente.query
                .filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_150, limite_90),
                )
            )
            total_sem_pedido_90_150 = total_sem_pedido_90_150_query.count()
        total_proximos_inativacao_query = (
            Cliente.query
            .filter(
                Cliente.ativo == True,
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.between(limite_180, limite_151),
            )
        )
        total_proximos_inativacao = total_proximos_inativacao_query.count()
        total_inativos_query = (
            Cliente.query
            .filter(
                Cliente.ativo == True,
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.between(limite_730, limite_181),
            )
        )
        if filtrar_carteira_por_vinculo:
            total_inativos_query = total_inativos_query.filter(
                Cliente.consultor_id.in_(operadores_ids_query)
            )
        total_inativos = total_inativos_query.count()
        if dashboard_tipo == "televendas":
            # Alinha com a mesma fonte usada na aba de Inativos.
            total_inativos = int(_total_inativos_badge(None) or 0)

        # Retornos vencidos: clientes com proxima_ligacao no passado (equipe não ligou).
        total_retorno_atrasado_query = (
            Cliente.query
            .filter(
                Cliente.ativo == True,
                Cliente.proxima_ligacao.isnot(None),
                Cliente.proxima_ligacao < agora,
            )
        )
        if filtrar_carteira_por_vinculo:
            total_retorno_atrasado_query = total_retorno_atrasado_query.filter(
                Cliente.consultor_id.in_(operadores_ids_query)
            )
        total_retorno_atrasado = total_retorno_atrasado_query.count()

        # Carteira em risco: proximos inativação (151-180d) sem qualquer ligação nos últimos 30d.
        limite_30d = agora - timedelta(days=30)
        ids_com_contato_recente = (
            db.session.query(Ligacao.cliente_id)
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(Ligacao.data_hora >= limite_30d)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
            .distinct()
            .subquery()
        )
        total_carteira_risco_query = (
            Cliente.query
            .filter(
                Cliente.ativo == True,
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.between(limite_180, limite_151),
                Cliente.id.notin_(ids_com_contato_recente),
            )
        )
        if filtrar_carteira_por_vinculo:
            total_carteira_risco_query = total_carteira_risco_query.filter(
                Cliente.consultor_id.in_(operadores_ids_query)
            )
        total_carteira_risco = total_carteira_risco_query.count()

        rows = (
            db.session.query(Usuario.nome, func.count(Ligacao.id))
            .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
            .filter(or_(Ligacao.data_hora >= desde, Ligacao.id == None))
            .group_by(Usuario.id, Usuario.nome)
            .order_by(desc(func.count(Ligacao.id)))
            .all()
        )
        ranking = [{"nome": n, "ligacoes": int(q or 0)} for n, q in rows]

        ult7 = (
            db.session.query(func.date(Ligacao.data_hora), func.count(Ligacao.id))
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(Ligacao.data_hora >= datetime.now() - timedelta(days=7))
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
            .group_by(func.date(Ligacao.data_hora))
            .order_by(func.date(Ligacao.data_hora))
            .all()
        )
        lig_por_dia = [{"data": d.strftime("%d/%m/%Y"), "data_iso": d.strftime("%Y-%m-%d"), "total": int(t)} for d, t in ult7]

        res = (
            db.session.query(Ligacao.resultado, func.count(Ligacao.id))
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(Ligacao.data_hora >= desde)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
            .group_by(Ligacao.resultado)
            .all()
        )
        resultados_chart = {(r or "nao_comprou"): int(c) for r, c in res}
        total_resultados_30d = sum(int(v or 0) for v in resultados_chart.values())
        total_vendas_30d = int(resultados_chart.get("comprou", 0))
        taxa_conversao_geral_30d = round(_percent(total_vendas_30d, total_resultados_30d), 1) if total_resultados_30d else 0.0

        progresso = []
        consultores = Usuario.query.filter_by(tipo=dashboard_tipo, ativo=True).order_by(Usuario.nome).all()
        for u in consultores:
            feitas = (
                db.session.query(func.count(Ligacao.id))
                .filter(Ligacao.consultor_id == u.id)
                .filter(func.date(Ligacao.data_hora) == hoje)
                .scalar()
            ) or 0
            meta = u.meta_diaria or 0
            perc = round(_percent(feitas, meta), 1) if meta else 0.0
            progresso.append({"id": u.id, "nome": u.nome, "meta": meta, "feitas": int(feitas), "percentual": perc})

        conv_rows = (
            db.session.query(
                Usuario.id,
                Usuario.nome,
                func.count(Ligacao.id).label("ligacoes"),
                func.sum(case((Ligacao.resultado == "comprou", 1), else_=0)).label("vendas"),
                func.sum(case((Ligacao.resultado == "comprou", Ligacao.valor_venda), else_=0)).label("receita"),
            )
            .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
            .filter(or_(Ligacao.data_hora >= desde, Ligacao.id == None))
            .group_by(Usuario.id, Usuario.nome)
            .order_by(desc("receita"))
            .all()
        )

        conversao = []
        for _, nome, ligs, vend, rec in conv_rows:
            ligs = int(ligs or 0)
            vend = int(vend or 0)
            receita_val = float(rec or 0)
            conv_pct = (vend / ligs * 100) if ligs else 0.0
            conversao.append(
                {
                    "nome": nome,
                    "ligacoes": ligs,
                    "vendas": vend,
                    "conversao": round(conv_pct, 1),
                    "receita": receita_val,
                    "receita_fmt": formatar_dinheiro(receita_val),
                }
            )

        meses_disponiveis = _ultimos_meses(12)

        return render_template(
            "supervisor.html",
            total_consultores=total_consultores,
            total_clientes=total_clientes,
            total_ligacoes=total_ligacoes,
            ligacoes_hoje=ligacoes_hoje,
            total_sem_pedido_90_150=total_sem_pedido_90_150,
            total_proximos_inativacao=total_proximos_inativacao,
            total_inativos=total_inativos,
            total_retorno_atrasado=total_retorno_atrasado,
            total_carteira_risco=total_carteira_risco,
            total_vendas_30d=total_vendas_30d,
            taxa_conversao_geral_30d=taxa_conversao_geral_30d,
            ranking=ranking,
            ligacoes_por_dia=lig_por_dia,
            resultados_chart=resultados_chart,
            progresso=progresso,
            consultores=consultores,
            conversao=conversao,
            dashboard_tipo=dashboard_tipo,
            dashboard_titulo=dashboard_titulo,
            mes_filtro=mes_filtro,
            ano_filtro=ano_filtro,
            meses_disponiveis=meses_disponiveis,
            mostrar_novidades=not current_user.viu_novidades,
            banners_ativos=get_banners_ativos(),
        )

    @app.route("/api/supervisor/ligacoes-por-mes")
    @login_required
    def api_supervisor_ligacoes_por_mes():
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "erro": "Acesso negado"}), 403

        try:
            mes = int(request.args.get("mes", datetime.now().month))
            ano = int(request.args.get("ano", datetime.now().year))
            consultor_id = request.args.get("consultor_id", type=int)

            if mes < 1 or mes > 12:
                return jsonify({"ok": False, "erro": "Mês inválido"}), 400

            inicio = datetime(ano, mes, 1)
            fim = datetime(ano + (1 if mes == 12 else 0), (1 if mes == 12 else mes + 1), 1)

            tipo_operador = (request.args.get("tipo") or "consultor").strip().lower()
            if tipo_operador not in ("consultor", "televendas"):
                return jsonify({"ok": False, "erro": "Tipo de dashboard inválido"}), 400

            consultor_nome = "Todos os operadores"
            if consultor_id:
                consultor = Usuario.query.filter_by(id=consultor_id, tipo=tipo_operador, ativo=True).first()
                if not consultor:
                    return jsonify({"ok": False, "erro": "Operador inválido"}), 400
                consultor_nome = consultor.nome

            query = (
                Ligacao.query.options(joinedload(Ligacao.consultor), joinedload(Ligacao.cliente))
                .join(Usuario, Usuario.id == Ligacao.consultor_id)
                .filter(Ligacao.data_hora >= inicio, Ligacao.data_hora < fim)
                .filter(Usuario.tipo == tipo_operador, Usuario.ativo == True)
            )
            if consultor_id:
                query = query.filter(Ligacao.consultor_id == consultor_id)

            ligacoes = query.order_by(Ligacao.data_hora.desc()).all()

            itens = []
            vendas = 0
            receita = 0.0
            for lig in ligacoes:
                resultado = lig.resultado or "nao_comprou"
                valor = float(lig.valor_venda or 0)
                if resultado == "comprou":
                    vendas += 1
                    receita += valor

                itens.append(
                    {
                        "id": lig.id,
                        "data_hora": lig.data_hora.strftime("%d/%m/%Y %H:%M"),
                        "consultor": lig.consultor.nome if lig.consultor else "-",
                        "cliente": lig.cliente.nome if lig.cliente else "-",
                        "contato": lig.contato_nome or "-",
                        "resultado": resultado,
                        "valor": valor,
                        "valor_fmt": formatar_dinheiro(valor),
                        "observacao": lig.observacao or "",
                    }
                )

            total = len(itens)
            conversao = _percent(vendas, total) if total else 0.0

            return jsonify(
                {
                    "ok": True,
                    "mes": mes,
                    "ano": ano,
                    "consultor_id": consultor_id,
                    "consultor_nome": consultor_nome,
                    "ligacoes": itens,
                    "estatisticas": {
                        "total_ligacoes": total,
                        "vendas": vendas,
                        "conversao": round(conversao, 1),
                        "receita": receita,
                        "receita_fmt": formatar_dinheiro(receita),
                    },
                }
            )
        except ValueError:
            return jsonify({"ok": False, "erro": "Parâmetros inválidos"}), 400
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    @app.route("/supervisor/usuarios")
    @login_required
    def gerenciar_usuarios():
        if current_user.tipo != "supervisor":
            flash("Acesso negado.", "danger")
            return redirect(url_for("index"))

        usuarios = Usuario.query.order_by(Usuario.nome.asc()).all()

        usuarios_data = []
        for u in usuarios:
            total_clientes = Cliente.query.filter_by(consultor_id=u.id, ativo=True).count() if u.tipo == "consultor" else 0
            usuarios_data.append(
                {
                    "id": u.id,
                    "nome": u.nome,
                    "email": u.email,
                    "tipo": u.tipo,
                    "ativo": u.ativo,
                    "meta_diaria": u.meta_diaria or 0,
                    "codigo_supervisor_tg650": u.codigo_supervisor_tg650,
                    "data_cadastro": u.data_cadastro,
                    "total_clientes": total_clientes,
                }
            )

        return render_template("gerenciar_usuarios.html", usuarios=usuarios_data)

    @app.route("/supervisor/usuarios/criar", methods=["POST"])
    @login_required
    def criar_usuario():
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            payload = request.get_json(silent=True) or {}
            nome = s(payload.get("nome"))
            email = s(payload.get("email"))
            senha = payload.get("senha") or ""
            tipo = s(payload.get("tipo"))
            meta_diaria = int(payload.get("meta_diaria") or 10)
            codigo_supervisor_tg650 = s(payload.get("codigo_supervisor_tg650"))

            if not nome or not email or not senha:
                return jsonify({"ok": False, "mensagem": "Nome, email e senha são obrigatórios"}), 400

            if tipo not in ("consultor", "supervisor", "televendas", "supervisor_repr"):
                return jsonify({"ok": False, "mensagem": "Tipo inválido"}), 400

            if Usuario.query.filter_by(email=email).first():
                return jsonify({"ok": False, "mensagem": "Email já cadastrado"}), 400

            novo_usuario = Usuario(
                nome=nome,
                email=email,
                senha_hash=generate_password_hash(senha),
                tipo=tipo,
                meta_diaria=meta_diaria,
                codigo_supervisor_tg650=codigo_supervisor_tg650 if tipo == "supervisor_repr" else None,
                ativo=True,
            )

            db.session.add(novo_usuario)
            db.session.commit()

            mensagem = f"Usuário {nome} criado com sucesso!"
            if tipo == "supervisor_repr" and codigo_supervisor_tg650:
                try:
                    sync_result = _sincronizar_vinculos_tg650_supervisor_repr(novo_usuario.id, codigo_supervisor_tg650)
                    if sync_result.get("ok"):
                        mensagem += f" TG650 sincronizada ({sync_result.get('novos', 0)} novos, {sync_result.get('atualizados', 0)} atualizados)."
                    else:
                        mensagem += f" TG650 não sincronizada: {sync_result.get('mensagem')}."
                except Exception as sync_err:
                    mensagem += f" TG650 não sincronizada: {str(sync_err)}."

            return jsonify({"ok": True, "mensagem": mensagem})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/usuarios/<int:usuario_id>/editar", methods=["POST"])
    @login_required
    def editar_usuario(usuario_id):
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            usuario = db.session.get(Usuario, usuario_id)
            if not usuario:
                return jsonify({"ok": False, "mensagem": "Usuário não encontrado"}), 404

            payload = request.get_json(silent=True) or {}
            nome = s(payload.get("nome"))
            email = s(payload.get("email"))
            tipo = s(payload.get("tipo"))
            meta_diaria = int(payload.get("meta_diaria") or 10)
            codigo_supervisor_tg650 = s(payload.get("codigo_supervisor_tg650"))

            if not nome or not email:
                return jsonify({"ok": False, "mensagem": "Nome e email são obrigatórios"}), 400

            if tipo not in ("consultor", "supervisor", "televendas", "supervisor_repr"):
                return jsonify({"ok": False, "mensagem": "Tipo inválido"}), 400

            email_existe = Usuario.query.filter(Usuario.email == email, Usuario.id != usuario_id).first()
            if email_existe:
                return jsonify({"ok": False, "mensagem": "Email já cadastrado por outro usuário"}), 400

            usuario.nome = nome
            usuario.email = email
            usuario.tipo = tipo
            usuario.meta_diaria = meta_diaria
            usuario.codigo_supervisor_tg650 = codigo_supervisor_tg650 if tipo == "supervisor_repr" else None

            db.session.commit()

            mensagem = f"Usuário {nome} atualizado com sucesso!"
            if tipo == "supervisor_repr" and codigo_supervisor_tg650:
                try:
                    sync_result = _sincronizar_vinculos_tg650_supervisor_repr(usuario.id, codigo_supervisor_tg650)
                    if sync_result.get("ok"):
                        mensagem += f" TG650 sincronizada ({sync_result.get('novos', 0)} novos, {sync_result.get('atualizados', 0)} atualizados)."
                    else:
                        mensagem += f" TG650 não sincronizada: {sync_result.get('mensagem')}."
                except Exception as sync_err:
                    mensagem += f" TG650 não sincronizada: {str(sync_err)}."

            return jsonify({"ok": True, "mensagem": mensagem})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/usuarios/<int:usuario_id>/toggle-status", methods=["POST"])
    @login_required
    def toggle_status_usuario(usuario_id):
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            usuario = db.session.get(Usuario, usuario_id)
            if not usuario:
                return jsonify({"ok": False, "mensagem": "Usuário não encontrado"}), 404

            if usuario.id == current_user.id:
                return jsonify({"ok": False, "mensagem": "Você não pode inativar sua própria conta"}), 400

            usuario.ativo = not usuario.ativo
            db.session.commit()

            status_texto = "ativado" if usuario.ativo else "inativado"
            return jsonify({"ok": True, "mensagem": f"Usuário {usuario.nome} {status_texto} com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/usuarios/<int:usuario_id>/redefinir-senha", methods=["POST"])
    @login_required
    def redefinir_senha_usuario(usuario_id):
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            usuario = db.session.get(Usuario, usuario_id)
            if not usuario:
                return jsonify({"ok": False, "mensagem": "Usuário não encontrado"}), 404

            payload = request.get_json(silent=True) or {}
            nova_senha = payload.get("nova_senha") or ""

            if not nova_senha or len(nova_senha) < 6:
                return jsonify({"ok": False, "mensagem": "Senha deve ter no mínimo 6 caracteres"}), 400

            usuario.senha_hash = generate_password_hash(nova_senha)
            db.session.commit()

            return jsonify({"ok": True, "mensagem": f"Senha de {usuario.nome} redefinida com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/banners")
    @login_required
    def gerenciar_banners():
        if current_user.tipo != "supervisor":
            return redirect(url_for("meus_clientes"))

        banners = Banner.query.options(joinedload(Banner.criador)).order_by(Banner.data_criacao.desc()).all()
        return render_template("gerenciar_banners.html", banners=banners)

    @app.route("/supervisor/banners/criar", methods=["POST"])
    @login_required
    def criar_banner():
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            payload = request.get_json(silent=True) or {}
            titulo = s(payload.get("titulo"))
            mensagem = s(payload.get("mensagem"))
            tipo = s(payload.get("tipo")) or "info"
            data_expiracao = payload.get("data_expiracao")

            if not titulo or not mensagem:
                return jsonify({"ok": False, "mensagem": "Título e mensagem são obrigatórios"}), 400

            if tipo not in ["info", "warning", "success", "danger"]:
                tipo = "info"

            expiracao_dt = None
            if data_expiracao:
                try:
                    expiracao_dt = datetime.strptime(data_expiracao, "%Y-%m-%d")
                    expiracao_dt = expiracao_dt.replace(hour=23, minute=59, second=59)
                except Exception:
                    return jsonify({"ok": False, "mensagem": "Data de expiração inválida"}), 400

            banner = Banner(
                titulo=titulo,
                mensagem=mensagem,
                tipo=tipo,
                criado_por=current_user.id,
                data_expiracao=expiracao_dt,
            )
            db.session.add(banner)
            db.session.commit()

            return jsonify({"ok": True, "mensagem": "Banner criado com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/banners/<int:banner_id>/toggle-status", methods=["POST"])
    @login_required
    def toggle_banner_status(banner_id):
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            banner = db.session.get(Banner, banner_id)
            if not banner:
                return jsonify({"ok": False, "mensagem": "Banner não encontrado"}), 404

            banner.ativo = not banner.ativo
            db.session.commit()

            status_texto = "ativado" if banner.ativo else "desativado"
            return jsonify({"ok": True, "mensagem": f"Banner {status_texto} com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/banners/<int:banner_id>/excluir", methods=["POST"])
    @login_required
    def excluir_banner(banner_id):
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            banner = db.session.get(Banner, banner_id)
            if not banner:
                return jsonify({"ok": False, "mensagem": "Banner não encontrado"}), 404

            db.session.delete(banner)
            db.session.commit()

            return jsonify({"ok": True, "mensagem": "Banner excluído com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/supervisores-representante")
    @login_required
    def gerenciar_supervisores_representante():
        if current_user.tipo != "supervisor":
            flash("Acesso negado.", "danger")
            return redirect(url_for("index"))

        supervisores_repr = Usuario.query.filter_by(tipo="supervisor_repr").order_by(Usuario.nome.asc()).all()

        supervisores_data = []
        for sup in supervisores_repr:
            vinculos_ativos = SupervisorRepresentanteVinculo.query.filter_by(
                supervisor_id=sup.id, 
                ativo=True
            ).count()
            
            supervisores_data.append({
                "id": sup.id,
                "nome": sup.nome,
                "email": sup.email,
                "ativo": sup.ativo,
                "codigo_supervisor_tg650": sup.codigo_supervisor_tg650,
                "data_cadastro": sup.data_cadastro,
                "total_vinculos": vinculos_ativos,
            })

        return render_template("gerenciar_supervisores_representante.html", supervisores=supervisores_data)

    @app.route("/supervisor/supervisores-representante/<int:supervisor_id>/vinculos")
    @login_required
    def listar_vinculos_supervisor_repr(supervisor_id):
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        supervisor = db.session.get(Usuario, supervisor_id)
        if not supervisor or supervisor.tipo != "supervisor_repr":
            return jsonify({"ok": False, "mensagem": "Supervisor de representante não encontrado"}), 404

        vinculos = SupervisorRepresentanteVinculo.query.filter_by(supervisor_id=supervisor_id).all()

        vinculos_data = [{
            "id": v.id,
            "codigo_representante": v.codigo_representante,
            "nome_representante": v.nome_representante,
            "ativo": v.ativo,
            "sincronizado_tg650": v.sincronizado_tg650,
            "data_cadastro": v.data_cadastro.strftime("%d/%m/%Y %H:%M") if v.data_cadastro else None,
        } for v in vinculos]

        return jsonify({
            "ok": True,
            "supervisor": {
                "id": supervisor.id,
                "nome": supervisor.nome,
                "codigo_supervisor_tg650": supervisor.codigo_supervisor_tg650,
            },
            "vinculos": vinculos_data
        })

    @app.route("/supervisor/supervisores-representante/<int:supervisor_id>/vinculos/adicionar", methods=["POST"])
    @login_required
    def adicionar_vinculo_supervisor_repr(supervisor_id):
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            supervisor = db.session.get(Usuario, supervisor_id)
            if not supervisor or supervisor.tipo != "supervisor_repr":
                return jsonify({"ok": False, "mensagem": "Supervisor de representante não encontrado"}), 404

            payload = request.get_json(silent=True) or {}
            codigo_representante = s(payload.get("codigo_representante"))
            nome_representante = s(payload.get("nome_representante"))

            if not codigo_representante:
                return jsonify({"ok": False, "mensagem": "Código do representante é obrigatório"}), 400

            vinculo_existente = SupervisorRepresentanteVinculo.query.filter_by(
                supervisor_id=supervisor_id,
                codigo_representante=codigo_representante
            ).first()

            if vinculo_existente:
                if not vinculo_existente.ativo:
                    vinculo_existente.ativo = True
                    db.session.commit()
                    return jsonify({"ok": True, "mensagem": "Vínculo reativado com sucesso!"})
                return jsonify({"ok": False, "mensagem": "Vínculo já existe"}), 400

            novo_vinculo = SupervisorRepresentanteVinculo(
                supervisor_id=supervisor_id,
                codigo_representante=codigo_representante,
                nome_representante=nome_representante,
                ativo=True,
                sincronizado_tg650=False
            )

            db.session.add(novo_vinculo)
            db.session.commit()

            return jsonify({"ok": True, "mensagem": "Vínculo adicionado com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/supervisores-representante/<int:supervisor_id>/vinculos/<int:vinculo_id>/remover", methods=["POST"])
    @login_required
    def remover_vinculo_supervisor_repr(supervisor_id, vinculo_id):
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            vinculo = db.session.get(SupervisorRepresentanteVinculo, vinculo_id)
            if not vinculo or vinculo.supervisor_id != supervisor_id:
                return jsonify({"ok": False, "mensagem": "Vínculo não encontrado"}), 404

            vinculo.ativo = False
            db.session.commit()

            return jsonify({"ok": True, "mensagem": "Vínculo removido com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/supervisores-representante/<int:supervisor_id>/sincronizar-tg650", methods=["POST"])
    @login_required
    def sincronizar_vinculos_tg650(supervisor_id):
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            supervisor = db.session.get(Usuario, supervisor_id)
            if not supervisor or supervisor.tipo != "supervisor_repr":
                return jsonify({"ok": False, "mensagem": "Supervisor de representante não encontrado"}), 404

            if not supervisor.codigo_supervisor_tg650:
                return jsonify({"ok": False, "mensagem": "Código TG650 não configurado para este supervisor"}), 400

            sync_result = _sincronizar_vinculos_tg650_supervisor_repr(supervisor_id, supervisor.codigo_supervisor_tg650)
            if not sync_result.get("ok"):
                return jsonify({"ok": False, "mensagem": sync_result.get("mensagem")}), 404

            return jsonify(sync_result)

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro ao sincronizar: {str(e)}"}), 500

