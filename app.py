import os
os.environ["OTEL_SDK_DISABLED"] = "true"

import time
from flask import Flask

from core.bootstrap_db import bootstrap_app_database
from core.config import apply_app_config, MAIL_RECIPIENTS
from core.extensions import db, login_manager, mail
from core.helpers import formatar_dinheiro_filter
from core.models import Usuario
from core.scheduler_runtime import start_scheduler_once
from routes.account_client_routes import register_account_client_routes
from routes.admin_routes import register_admin_routes
from routes.auth_routes import register_auth_routes
from routes.clientes_ligacoes_routes import register_clientes_ligacoes_routes
from routes.error_handlers import register_error_handlers
from routes.oracle_routes import register_oracle_routes
from routes.supervisor_routes import register_supervisor_routes
from services.report_service import enviar_relatorio_email

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
mail.init_app(app)
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'login'
app.jinja_env.filters['formatar_dinheiro'] = formatar_dinheiro_filter


@app.after_request
def garantir_charset_utf8(response):
    ctype = (response.headers.get("Content-Type") or "").lower()
    if ("text/html" in ctype or "text/plain" in ctype) and "charset=" not in ctype:
        response.headers["Content-Type"] = f"{response.mimetype}; charset=utf-8"
    return response


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Usuario, int(user_id))

register_auth_routes(app)
register_account_client_routes(app)
register_admin_routes(app)
register_clientes_ligacoes_routes(app)
register_oracle_routes(app)
register_supervisor_routes(app)

register_error_handlers(app)

with app.app_context():
    bootstrap_app_database()

# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    from waitress import serve

    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        start_scheduler_once(app, enviar_relatorio_email, MAIL_RECIPIENTS)

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))

    app.logger.info("Servidor de produção iniciado, Controle de Ligações em http://%s:%s", host, port)
    serve(app, host=host, port=port, threads=32)
