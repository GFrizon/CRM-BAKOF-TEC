import os

from dotenv import load_dotenv

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(APP_DIR, ".env"))

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "controle_ligacoes")
SECRET_KEY = os.getenv("SECRET_KEY", "troque-esta-chave-por-uma-bem-grande")
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "20"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))
DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "15"))
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))

DB_URI = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.office365.com")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "false").lower() == "true"
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "indicadores@bakof.com.br")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_DEFAULT_NAME = os.getenv("MAIL_DEFAULT_NAME", "Indicadores Bakof")
MAIL_DEFAULT_FROM = os.getenv("MAIL_DEFAULT_FROM", "indicadores@bakof.com.br")
MAIL_RECIPIENTS = [
    e.strip()
    for e in os.getenv("MAIL_RECIPIENTS", "gabriel.frizon@bakof.com.br").split(",")
    if e.strip()
]

APP_VERSION = os.getenv("APP_VERSION", "v3.0")
APP_RELEASE_DATE = os.getenv("APP_RELEASE_DATE", "13/04/2026")
SUPERVISOR_SECRET_HEALTH_KEY = os.getenv("SUPERVISOR_SECRET_HEALTH_KEY", "")
SUPERVISOR_DEV_PANEL_PASSWORD = os.getenv("SUPERVISOR_DEV_PANEL_PASSWORD", "")
CRANIO_WIDGET_ENABLED = os.getenv("CRANIO_WIDGET_ENABLED", "0").lower() in ("1", "true", "yes", "on")
CRANIO_AI_SUMMARY_ENABLED = os.getenv("CRANIO_AI_SUMMARY_ENABLED", "0").lower() in ("1", "true", "yes", "on")
CRANIO_AI_SUMMARY_AUTO_DAILY = os.getenv("CRANIO_AI_SUMMARY_AUTO_DAILY", "0").lower() in ("1", "true", "yes", "on")


def apply_app_config(app):
    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": DB_POOL_SIZE,
        "max_overflow": DB_MAX_OVERFLOW,
        "pool_timeout": DB_POOL_TIMEOUT,
        "pool_recycle": DB_POOL_RECYCLE,
        "pool_pre_ping": True,
    }
    app.config.update(
        MAIL_SERVER=MAIL_SERVER,
        MAIL_PORT=MAIL_PORT,
        MAIL_USE_TLS=MAIL_USE_TLS,
        MAIL_USE_SSL=MAIL_USE_SSL,
        MAIL_USERNAME=MAIL_USERNAME,
        MAIL_PASSWORD=MAIL_PASSWORD,
        MAIL_DEFAULT_SENDER=(MAIL_DEFAULT_NAME, MAIL_DEFAULT_FROM),
        SUPERVISOR_SECRET_HEALTH_KEY=SUPERVISOR_SECRET_HEALTH_KEY,
        SUPERVISOR_DEV_PANEL_PASSWORD=SUPERVISOR_DEV_PANEL_PASSWORD,
        CRANIO_WIDGET_ENABLED=CRANIO_WIDGET_ENABLED,
        CRANIO_AI_SUMMARY_ENABLED=CRANIO_AI_SUMMARY_ENABLED,
        CRANIO_AI_SUMMARY_AUTO_DAILY=CRANIO_AI_SUMMARY_AUTO_DAILY,
    )
