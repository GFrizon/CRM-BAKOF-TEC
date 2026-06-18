import os
os.environ["OTEL_SDK_DISABLED"] = "true"

import logging
import time
from logging.handlers import RotatingFileHandler

from flask import Flask, request
from flask_login import current_user

from core.activity_tracker import mark_user_activity
from core.bootstrap_db import bootstrap_app_database
from core.config import apply_app_config, MAIL_RECIPIENTS, APP_VERSION, APP_RELEASE_DATE
from core.extensions import db, login_manager, mail
from core.helpers import formatar_dinheiro_filter
from core.models import Usuario
from core.scheduler_runtime import start_scheduler_once
from routes.account_client_routes import register_account_client_routes
from routes.admin_routes import register_admin_routes
from routes.auth_routes import register_auth_routes
from routes.campanhas_routes import register_campanhas_routes
from routes.premiacao_extra_routes import register_premiacao_extra_routes
from routes.clientes_ligacoes_routes import register_clientes_ligacoes_routes
from routes.cranio_routes import register_cranio_routes
from routes.error_handlers import register_error_handlers
from routes.oracle_routes import register_oracle_routes
from routes.supervisor_routes import register_supervisor_routes
from routes.sse_routes import register_sse_routes
from services.report_service import enviar_relatorio_email
from services.warmup_service import iniciar_warmup_oracle

# Fuso horário São Paulo
os.environ['TZ'] = 'America/Sao_Paulo'
try:
    time.tzset()
except AttributeError:
    pass

# =============================================================================
# APP
# =============================================================================
app = Flask(__name__, template_folder='templates', static_folder='static')
apply_app_config(app)
app.config["JSON_AS_ASCII"] = False

os.makedirs("logs", exist_ok=True)
_file_handler = RotatingFileHandler("logs/app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
_file_handler.setLevel(logging.WARNING)
_file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
app.logger.addHandler(_file_handler)
app.logger.setLevel(logging.WARNING)
mail.init_app(app)
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'login'
app.jinja_env.filters['formatar_dinheiro'] = formatar_dinheiro_filter

@app.context_processor
def inject_app_meta():
    return {
        "app_version": APP_VERSION,
        "app_release_date": APP_RELEASE_DATE,
    }


@app.after_request
def garantir_charset_utf8(response):
    ctype = (response.headers.get("Content-Type") or "").lower()
    if ("text/html" in ctype or "text/plain" in ctype) and "charset=" not in ctype:
        response.headers["Content-Type"] = f"{response.mimetype}; charset=utf-8"
    return response


@app.before_request
def registrar_atividade_usuario():
    if not getattr(current_user, "is_authenticated", False):
        return
    try:
        mark_user_activity(
            user_id=int(current_user.id),
            nome=str(current_user.nome or ""),
            tipo=str(current_user.tipo or ""),
            ip=str(request.headers.get("X-Forwarded-For") or request.remote_addr or ""),
            path=str(request.path or ""),
        )
    except Exception:
        # Telemetria best-effort; nunca deve impactar o fluxo principal.
        pass


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Usuario, int(user_id))

register_auth_routes(app)
register_account_client_routes(app)
register_admin_routes(app)
register_campanhas_routes(app)
register_premiacao_extra_routes(app)
register_clientes_ligacoes_routes(app)
register_cranio_routes(app)
register_oracle_routes(app)
register_supervisor_routes(app)
register_sse_routes(app)

register_error_handlers(app)

with app.app_context():
    bootstrap_app_database()

if os.getenv("DISABLE_APP_WARMUP", "0") != "1":
    iniciar_warmup_oracle(app)


def _inicializar_scheduler():
    # Permite desativar explicitamente em ambientes com scheduler externo.
    if os.getenv("DISABLE_APP_SCHEDULER", "0") == "1":
        app.logger.info("Scheduler desativado por DISABLE_APP_SCHEDULER=1")
        return

    # Evita inicializacao duplicada no processo pai do reloader.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    start_scheduler_once(app, enviar_relatorio_email, MAIL_RECIPIENTS)


_inicializar_scheduler()

# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    from waitress import serve

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))

    app.logger.info("Servidor de produção iniciado, Controle de Ligações em http://%s:%s", host, port)
    serve(app, host=host, port=port, threads=32)
