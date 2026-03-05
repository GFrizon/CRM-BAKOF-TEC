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


def apply_app_config(app):
    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config.update(
        MAIL_SERVER=MAIL_SERVER,
        MAIL_PORT=MAIL_PORT,
        MAIL_USE_TLS=MAIL_USE_TLS,
        MAIL_USE_SSL=MAIL_USE_SSL,
        MAIL_USERNAME=MAIL_USERNAME,
        MAIL_PASSWORD=MAIL_PASSWORD,
        MAIL_DEFAULT_SENDER=(MAIL_DEFAULT_NAME, MAIL_DEFAULT_FROM),
    )
