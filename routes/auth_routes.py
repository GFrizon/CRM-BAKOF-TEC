from datetime import datetime, timedelta

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_mail import Message
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from core.extensions import db, mail
from core.helpers import s
from core.models import Usuario


def register_auth_routes(app):
    def _reset_serializer():
        return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset-v1")

    def _build_reset_token(user):
        payload = {"uid": user.id, "pwd_sig": (user.senha_hash or "")[:20]}
        return _reset_serializer().dumps(payload)

    def _load_user_by_reset_token(token, max_age_seconds=1800):
        try:
            payload = _reset_serializer().loads(token, max_age=max_age_seconds)
        except SignatureExpired:
            return None, "Token expirado. Solicite um novo link."
        except BadSignature:
            return None, "Token invalido. Solicite um novo link."

        user_id = payload.get("uid")
        pwd_sig = payload.get("pwd_sig", "")
        if not user_id:
            return None, "Token invalido. Solicite um novo link."

        user = db.session.get(Usuario, int(user_id))
        if not user or not user.ativo:
            return None, "Usuario nao encontrado ou inativo."
        if (user.senha_hash or "")[:20] != pwd_sig:
            return None, "Este link ja foi utilizado ou esta invalido."
        return user, None

    def _enviar_email_recuperacao_senha(user):
        token = _build_reset_token(user)
        link = url_for("resetar_senha", token=token, _external=True)

        assunto = "CRM Bakof - Recuperacao de senha"
        html = (
            f"<p>Ola, {user.nome}.</p>"
            "<p>Recebemos uma solicitacao para redefinir sua senha no CRM.</p>"
            f"<p><a href='{link}' "
            "style='display:inline-block;padding:10px 14px;background:#2563eb;color:#fff;"
            "text-decoration:none;border-radius:8px;font-weight:600'>Redefinir senha</a></p>"
            "<p>Este link expira em 30 minutos.</p>"
            "<p>Se voce nao solicitou, ignore este e-mail.</p>"
        )
        texto = (
            f"Ola, {user.nome}.\n\n"
            "Recebemos uma solicitacao para redefinir sua senha no CRM.\n"
            f"Acesse: {link}\n\n"
            "Este link expira em 30 minutos.\n"
            "Se voce nao solicitou, ignore este e-mail."
        )

        msg = Message(subject=assunto, recipients=[user.email], body=texto, html=html)
        mail.send(msg)

    @app.route("/")
    def index():
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        return redirect(url_for("dashboard_supervisor" if current_user.tipo == "supervisor" else "meus_clientes"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("index"))

        if request.method == "POST":
            email = s(request.form.get("email"))
            senha = request.form.get("senha") or ""
            lembrar = (request.form.get("lembrar") == "1")

            user = Usuario.query.filter_by(email=email, ativo=True).first()
            if not user:
                flash("Usuario nao encontrado ou inativo.", "danger")
                return render_template("login.html", lembrar=lembrar)

            try:
                okpwd = check_password_hash(user.senha_hash, senha)
            except Exception:
                okpwd = False

            if not okpwd:
                flash("Senha invalida.", "danger")
                return render_template("login.html", lembrar=lembrar)

            login_user(user, remember=lembrar, duration=timedelta(days=30) if lembrar else None)
            flash("Login realizado com sucesso!", "success")
            return redirect(url_for("index"))

        return render_template("login.html", lembrar=False)

    @app.route("/esqueci-senha", methods=["GET", "POST"])
    def esqueci_senha():
        if current_user.is_authenticated:
            return redirect(url_for("index"))

        if request.method == "POST":
            email = s(request.form.get("email")).lower()
            user = Usuario.query.filter_by(email=email, ativo=True).first()

            if user:
                try:
                    _enviar_email_recuperacao_senha(user)
                except Exception as e:
                    app.logger.error("Erro ao enviar e-mail de recuperacao para %s: %s", email, str(e))

            flash(
                "Se o e-mail existir e estiver ativo, enviaremos um link de recuperacao.",
                "info",
            )
            return redirect(url_for("login"))

        return render_template("forgot_password.html")

    @app.route("/redefinir-senha/<token>", methods=["GET", "POST"])
    def resetar_senha(token):
        if current_user.is_authenticated:
            return redirect(url_for("index"))

        user, erro_token = _load_user_by_reset_token(token)
        if erro_token:
            flash(erro_token, "danger")
            return redirect(url_for("esqueci_senha"))

        if request.method == "POST":
            senha = request.form.get("senha") or ""
            confirmar = request.form.get("confirmar_senha") or ""

            if len(senha) < 6:
                flash("Senha deve ter no minimo 6 caracteres.", "danger")
                return render_template("reset_password.html", token=token)
            if senha != confirmar:
                flash("As senhas nao conferem.", "danger")
                return render_template("reset_password.html", token=token)

            try:
                user.senha_hash = generate_password_hash(senha)
                db.session.commit()
                flash("Senha redefinida com sucesso. Faça login.", "success")
                return redirect(url_for("login"))
            except Exception as e:
                db.session.rollback()
                app.logger.error("Erro ao redefinir senha para usuario %s: %s", user.id, str(e))
                flash("Nao foi possivel redefinir a senha. Tente novamente.", "danger")
                return render_template("reset_password.html", token=token)

        return render_template("reset_password.html", token=token)

    @app.route("/logout")
    def logout():
        if current_user.is_authenticated:
            logout_user()
            flash("Voce saiu do sistema.", "info")
        return redirect(url_for("login"))

    @app.route("/marcar-novidades-vistas", methods=["POST"])
    @login_required
    def marcar_novidades_vistas():
        try:
            current_user.viu_novidades = True
            db.session.commit()
            return jsonify({"ok": True})
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": str(e)}), 500

    @app.route("/ajuda-suporte")
    @login_required
    def ajuda_suporte():
        if current_user.tipo == "representante":
            return redirect(url_for("meus_clientes", visao="dashboard", aba="oracle"))
        return render_template("ajuda_suporte.html")

    @app.route("/enviar-notificacao-whatsapp", methods=["POST"])
    @login_required
    def enviar_notificacao_whatsapp():
        try:
            data = request.get_json()
            tipo = data.get('tipo', 'duvida')
            mensagem = data.get('mensagem', '')
            
            if not mensagem.strip():
                return jsonify({"ok": False, "mensagem": "Mensagem não pode estar vazia"}), 400
            
            # Formatar mensagem para WhatsApp
            texto_formatado = f"""*CRM Bakof - Nova Mensagem de Suporte*%0A%0A*Usuário:* {current_user.nome}%0A*Tipo:* {tipo.title()}%0A*Data/Hora:* {datetime.now().strftime('%d/%m/%Y %H:%M')}%0A%0A*Mensagem:*%0A{mensagem}%0A%0A---
Enviado pelo sistema CRM Bakof v3.0"""
            
            # Criar link wa.me direto
            whatsapp_link = f"https://wa.me/5537449976?text={texto_formatado}"
            
            app.logger.info(f"Mensagem WhatsApp preparada para {current_user.nome}")
            
            return jsonify({
                "ok": True, 
                "mensagem": "Abrindo WhatsApp com sua mensagem...",
                "whatsapp_link": whatsapp_link
            })
            
        except Exception as e:
            app.logger.error(f"Erro ao preparar mensagem WhatsApp: {str(e)}")
            return jsonify({"ok": False, "mensagem": str(e)}), 500

    @app.route("/testar-whatsapp")
    @login_required
    def testar_whatsapp():
        """Rota para testar a conexão com WhatsApp"""
        try:
            from services.whatsapp_service import whatsapp_service
            resultado = whatsapp_service.testar_conexao()
            return jsonify(resultado)
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Erro ao testar: {str(e)}"
            })
