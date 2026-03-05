from flask import flash, redirect, url_for

from core.extensions import db


def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(error):
        db.session.rollback()
        flash("Pagina nao encontrada.", "warning")
        return redirect(url_for("index"))

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        flash("Erro interno do servidor. Contate o suporte.", "danger")
        return redirect(url_for("index"))
