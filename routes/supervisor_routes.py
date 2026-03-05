from datetime import datetime, timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, desc, func, or_
from sqlalchemy.orm import joinedload
from werkzeug.security import generate_password_hash

from core.extensions import db
from core.helpers import _percent, formatar_dinheiro, s
from core.models import Banner, Cliente, Ligacao, Usuario


def get_banners_ativos():
    agora = datetime.now()
    return (
        Banner.query.filter(Banner.ativo == True)
        .filter(or_(Banner.data_expiracao == None, Banner.data_expiracao >= agora))
        .order_by(Banner.data_criacao.desc())
        .all()
    )


def register_supervisor_routes(app):
    @app.route("/supervisor", endpoint="dashboard_supervisor")
    @login_required
    def supervisor_dashboard():
        if current_user.tipo != "supervisor":
            return redirect(url_for("meus_clientes"))

        mes_filtro = int(request.args.get("mes", datetime.now().month))
        ano_filtro = int(request.args.get("ano", datetime.now().year))

        hoje = datetime.now().date()
        desde = datetime.now() - timedelta(days=30)

        total_consultores = Usuario.query.filter_by(tipo="consultor", ativo=True).count()
        total_clientes = Cliente.query.filter_by(ativo=True).count()
        total_ligacoes = Ligacao.query.count()
        ligacoes_hoje = Ligacao.query.filter(func.date(Ligacao.data_hora) == hoje).count()

        rows = (
            db.session.query(Usuario.nome, func.count(Ligacao.id))
            .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
            .filter(Usuario.tipo == "consultor", Usuario.ativo == True)
            .filter(or_(Ligacao.data_hora >= desde, Ligacao.id == None))
            .group_by(Usuario.id, Usuario.nome)
            .order_by(desc(func.count(Ligacao.id)))
            .all()
        )
        ranking = [{"nome": n, "ligacoes": int(q or 0)} for n, q in rows]

        ult7 = (
            db.session.query(func.date(Ligacao.data_hora), func.count(Ligacao.id))
            .filter(Ligacao.data_hora >= datetime.now() - timedelta(days=7))
            .group_by(func.date(Ligacao.data_hora))
            .order_by(func.date(Ligacao.data_hora))
            .all()
        )
        lig_por_dia = [{"data": d.strftime("%d/%m/%Y"), "data_iso": d.strftime("%Y-%m-%d"), "total": int(t)} for d, t in ult7]

        res = (
            db.session.query(Ligacao.resultado, func.count(Ligacao.id))
            .filter(Ligacao.data_hora >= desde)
            .group_by(Ligacao.resultado)
            .all()
        )
        resultados_chart = {(r or "nao_comprou"): int(c) for r, c in res}

        progresso = []
        consultores = Usuario.query.filter_by(tipo="consultor", ativo=True).order_by(Usuario.nome).all()
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
            .filter(Usuario.tipo == "consultor", Usuario.ativo == True)
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
                    "receita_fmt": f"{receita_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                }
            )

        meses_disponiveis = []
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
        for i in range(12):
            data = data_atual - timedelta(days=30 * i)
            meses_disponiveis.append({"mes": data.month, "ano": data.year, "texto": f"{meses_nomes[data.month]}/{data.year}"})

        return render_template(
            "supervisor.html",
            total_consultores=total_consultores,
            total_clientes=total_clientes,
            total_ligacoes=total_ligacoes,
            ligacoes_hoje=ligacoes_hoje,
            ranking=ranking,
            ligacoes_por_dia=lig_por_dia,
            resultados_chart=resultados_chart,
            progresso=progresso,
            consultores=consultores,
            conversao=conversao,
            mes_filtro=mes_filtro,
            ano_filtro=ano_filtro,
            meses_disponiveis=meses_disponiveis,
            mostrar_novidades=not current_user.viu_novidades,
            banners_ativos=get_banners_ativos(),
        )

    @app.route("/ligacoes-dia/<string:data>")
    def ligacoes_dia(data):
        if not current_user.is_authenticated or current_user.tipo != "supervisor":
            return jsonify({"erro": "Acesso negado"}), 403

        try:
            data_obj = datetime.strptime(data, "%Y-%m-%d").date()

            ligacoes = (
                Ligacao.query.options(joinedload(Ligacao.consultor), joinedload(Ligacao.cliente))
                .filter(func.date(Ligacao.data_hora) == data_obj)
                .order_by(Ligacao.data_hora.desc())
                .all()
            )

            resultado = []
            for lig in ligacoes:
                resultado.append(
                    {
                        "hora": lig.data_hora.strftime("%H:%M"),
                        "consultor": lig.consultor.nome if lig.consultor else "",
                        "cliente": lig.cliente.nome if lig.cliente else "",
                        "contato": lig.contato_nome or "-",
                        "resultado": lig.resultado or "nao_comprou",
                        "valor": formatar_dinheiro(lig.valor_venda or 0),
                        "observacao": lig.observacao or "",
                    }
                )

            return jsonify(resultado)

        except Exception as e:
            return jsonify({"erro": str(e)}), 500

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

            if not nome or not email or not senha:
                return jsonify({"ok": False, "mensagem": "Nome, email e senha são obrigatórios"}), 400

            if tipo not in ("consultor", "supervisor"):
                return jsonify({"ok": False, "mensagem": "Tipo inválido"}), 400

            if Usuario.query.filter_by(email=email).first():
                return jsonify({"ok": False, "mensagem": "Email já cadastrado"}), 400

            novo_usuario = Usuario(
                nome=nome,
                email=email,
                senha_hash=generate_password_hash(senha),
                tipo=tipo,
                meta_diaria=meta_diaria,
                ativo=True,
            )

            db.session.add(novo_usuario)
            db.session.commit()

            return jsonify({"ok": True, "mensagem": f"Usuário {nome} criado com sucesso!"})

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

            if not nome or not email:
                return jsonify({"ok": False, "mensagem": "Nome e email são obrigatórios"}), 400

            if tipo not in ("consultor", "supervisor"):
                return jsonify({"ok": False, "mensagem": "Tipo inválido"}), 400

            email_existe = Usuario.query.filter(Usuario.email == email, Usuario.id != usuario_id).first()
            if email_existe:
                return jsonify({"ok": False, "mensagem": "Email já cadastrado por outro usuário"}), 400

            usuario.nome = nome
            usuario.email = email
            usuario.tipo = tipo
            usuario.meta_diaria = meta_diaria

            db.session.commit()

            return jsonify({"ok": True, "mensagem": f"Usuário {nome} atualizado com sucesso!"})

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
