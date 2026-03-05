from datetime import timedelta

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

            user = Usuario.query.filter_by(email=email, ativo=True).first()
            if not user:
                flash("Usuario nao encontrado ou inativo.", "danger")
                return render_template("login.html")

            try:
                okpwd = check_password_hash(user.senha_hash, senha)
            except Exception:
                okpwd = False

            if not okpwd:
                flash("Senha invalida.", "danger")
                return render_template("login.html")

            login_user(user, remember=False, duration=timedelta(hours=4))
            flash("Login realizado com sucesso!", "success")
            return redirect(url_for("index"))

        return render_template("login.html")

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
