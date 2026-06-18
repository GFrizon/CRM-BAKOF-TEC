import traceback

from flask import flash, jsonify, redirect, request, url_for

from core.extensions import db


def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(error):
        db.session.rollback()
        aceita_html = request.accept_mimetypes.accept_html
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        is_tecnico = (
            request.path == "/favicon.ico"
            or request.path.startswith("/static/")
            or request.path.startswith("/api/")
            or is_ajax
            or not aceita_html
        )
        if is_tecnico:
            return "", 404
        flash("Pagina nao encontrada.", "warning")
        return redirect(url_for("index"))

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        app.logger.error(
            "Erro 500 — %s %s | usuario=%s\n%s",
            request.method,
            request.full_path,
            getattr(request, "_cached_user", None) or "?",
            traceback.format_exc(),
        )
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        is_api = request.path.startswith("/api/")
        if is_ajax or is_api:
            return jsonify({"erro": "Erro interno do servidor."}), 500
        flash("Erro interno do servidor. Contate o suporte.", "danger")
        return redirect(url_for("index"))
