from datetime import datetime, timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from core.extensions import db
from core.helpers import s
from core.models import Usuario


def register_auth_routes(app):
    @app.route("/")
    def index():
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        return redirect(url_for("dashboard_supervisor" if current_user.tipo == "supervisor" else "meus_clientes"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
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
