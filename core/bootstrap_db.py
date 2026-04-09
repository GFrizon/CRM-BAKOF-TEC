import logging

from sqlalchemy import text

from core.config import MAIL_PASSWORD, MAIL_RECIPIENTS
from core.extensions import db
from core.models import Banner

logger = logging.getLogger(__name__)


def _column_exists(table_name, column_name):
    row = db.session.execute(
        text(
            """
            SELECT 1
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            LIMIT 1
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).first()
    return row is not None


def _index_exists(table_name, index_name):
    try:
        row = db.session.execute(
            text(
                """
                SELECT 1
                FROM INFORMATION_SCHEMA.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :table_name
                  AND INDEX_NAME = :index_name
                LIMIT 1
                """
            ),
            {"table_name": table_name, "index_name": index_name},
        ).first()
        return row is not None
    except Exception:
        db.session.rollback()
        return False


def _run_ddl(sql, ok_msg=None):
    try:
        db.session.execute(text(sql))
        db.session.commit()
        if ok_msg:
            logger.info(ok_msg)
    except Exception as e:
        db.session.rollback()
        logger.warning("Erro ao executar DDL: %s - %s", sql, e)


def _ensure_index(table_name, index_name, columns_sql):
    if _index_exists(table_name, index_name):
        return
    _run_ddl(
        f"ALTER TABLE {table_name} ADD INDEX {index_name} ({columns_sql})",
        ok_msg=f"[OK] Indice criado: {table_name}.{index_name} ({columns_sql})",
    )


def _parse_mysql_enum_values(column_type):
    if not column_type:
        return []
    normalized = str(column_type).strip()
    if not normalized.lower().startswith("enum(") or not normalized.endswith(")"):
        return []
    raw_values = normalized[5:-1]
    values = []
    for item in raw_values.split(","):
        cleaned = item.strip().strip("'").replace("\\'", "'")
        if cleaned:
            values.append(cleaned)
    return values


def _get_column_type(table_name, column_name):
    row = db.session.execute(
        text(
            """
            SELECT COLUMN_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            LIMIT 1
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).first()
    return row[0] if row else None


def _ensure_usuarios_tipo_enum():
    required_values = ["consultor", "supervisor", "televendas", "supervisor_repr"]
    try:
        column_type = _get_column_type("usuarios", "tipo")
        current_values = _parse_mysql_enum_values(column_type)
        merged_values = list(current_values)

        for value in required_values:
            if value not in merged_values:
                merged_values.append(value)

        if merged_values == current_values:
            logger.info("Campo usuarios.tipo ja contem os valores necessarios no ENUM")
            return

        enum_sql = ",".join(f"'{value}'" for value in merged_values)
        db.session.execute(
            text(
                "ALTER TABLE usuarios MODIFY COLUMN tipo "
                f"ENUM({enum_sql}) "
                "NOT NULL DEFAULT 'consultor'"
            )
        )
        db.session.commit()
        logger.info("Campo usuarios.tipo atualizado com ENUM compativel")
    except Exception as e:
        db.session.rollback()
        logger.warning("Erro ao atualizar enum usuarios.tipo (pode ja estar atualizado): %s", e)


def bootstrap_app_database():
    db.create_all()

    if not _column_exists("usuarios", "meta_diaria"):
        _run_ddl("ALTER TABLE usuarios ADD COLUMN meta_diaria INT DEFAULT 10")

    try:
        db.session.execute(text("UPDATE usuarios SET meta_diaria = 10 WHERE meta_diaria IS NULL"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    if not _column_exists("usuarios", "viu_novidades"):
        _run_ddl("ALTER TABLE usuarios ADD COLUMN viu_novidades BOOLEAN DEFAULT FALSE")

    try:
        db.session.execute(text("UPDATE usuarios SET viu_novidades = FALSE WHERE viu_novidades IS NULL"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    if not _column_exists("clientes", "origem"):
        _run_ddl(
            "ALTER TABLE clientes ADD COLUMN origem ENUM('importado_csv','manual') NOT NULL DEFAULT 'manual'"
        )

    if not _column_exists("clientes", "telefone2"):
        _run_ddl("ALTER TABLE clientes ADD COLUMN telefone2 VARCHAR(20)")

    _ensure_usuarios_tipo_enum()

    try:
        db.session.execute(
            text(
                "ALTER TABLE ligacoes MODIFY COLUMN resultado "
                "ENUM('comprou','nao_comprou','retornar','sem_interesse','relacionamento','cliente_inativo') "
                "NOT NULL DEFAULT 'nao_comprou'"
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()

    try:
        Banner.__table__.create(db.engine)
        db.session.commit()
    except Exception:
        db.session.rollback()

    campos_oracle = [
        ("cd_cliente_oracle", "ALTER TABLE clientes ADD COLUMN cd_cliente_oracle VARCHAR(50)"),
        ("categoria_consultor", "ALTER TABLE clientes ADD COLUMN categoria_consultor VARCHAR(100)"),
        ("conceito", "ALTER TABLE clientes ADD COLUMN conceito VARCHAR(20)"),
        ("ultimo_pedido_oracle", "ALTER TABLE clientes ADD COLUMN ultimo_pedido_oracle DATETIME"),
        ("valor_ultimo_pedido", "ALTER TABLE clientes ADD COLUMN valor_ultimo_pedido DECIMAL(12,2)"),
        ("situacao_ultimo_pedido", "ALTER TABLE clientes ADD COLUMN situacao_ultimo_pedido VARCHAR(50)"),
        ("representante_oracle", "ALTER TABLE clientes ADD COLUMN representante_oracle VARCHAR(200)"),
        ("municipio", "ALTER TABLE clientes ADD COLUMN municipio VARCHAR(120)"),
        ("uf", "ALTER TABLE clientes ADD COLUMN uf VARCHAR(2)"),
        ("contato", "ALTER TABLE clientes ADD COLUMN contato VARCHAR(200)"),
        ("valor_total_365dias", "ALTER TABLE clientes ADD COLUMN valor_total_365dias DECIMAL(12,2)"),
        ("data_ultima_sincronizacao", "ALTER TABLE clientes ADD COLUMN data_ultima_sincronizacao DATETIME"),
        ("em_atendimento_por", "ALTER TABLE clientes ADD COLUMN em_atendimento_por INT NULL"),
        ("em_atendimento_ate", "ALTER TABLE clientes ADD COLUMN em_atendimento_ate DATETIME NULL"),
    ]

    for column_name, campo_sql in campos_oracle:
        if not _column_exists("clientes", column_name):
            _run_ddl(campo_sql, ok_msg=f"[OK] Campo Oracle adicionado: {column_name}")

    # Indices para acelerar filtros/contagens das carteiras e dashboards.
    _ensure_index("clientes", "idx_clientes_ativo_consultor", "ativo, consultor_id")
    _ensure_index("clientes", "idx_clientes_ativo_proxima", "ativo, proxima_ligacao")
    _ensure_index("clientes", "idx_clientes_ativo_ultimo_pedido", "ativo, ultimo_pedido_oracle")
    _ensure_index("clientes", "idx_clientes_cd_oracle_ativo", "cd_cliente_oracle, ativo")
    _ensure_index("ligacoes", "idx_ligacoes_cliente_consultor_data", "cliente_id, consultor_id, data_hora")
    _ensure_index("ligacoes", "idx_ligacoes_consultor_data", "consultor_id, data_hora")

    if not MAIL_PASSWORD:
        logger.warning("MAIL_PASSWORD nao configurado. Email nao funcionara.")
        logger.warning("Configure a variavel MAIL_PASSWORD no .env")

    if not MAIL_RECIPIENTS:
        logger.warning("Nenhum destinatario configurado para relatorios.")
    else:
        logger.info("Email configurado. Destinatarios: %s", ", ".join(MAIL_RECIPIENTS))
