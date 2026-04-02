-- ============================================================================
-- MIGRATION IDEMPOTENTE: Supervisor de Representante
-- Seguro para executar múltiplas vezes (dev/prod)
-- ============================================================================

USE controle_ligacoes;

-- 1) Garantir valor supervisor_repr no ENUM usuarios.tipo
ALTER TABLE usuarios
MODIFY COLUMN tipo ENUM('consultor', 'supervisor', 'televendas', 'supervisor_repr')
NOT NULL DEFAULT 'consultor';

-- 2) Garantir coluna usuarios.codigo_supervisor_tg650
SET @col_exists := (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'usuarios'
      AND COLUMN_NAME = 'codigo_supervisor_tg650'
);

SET @sql_add_col := IF(
    @col_exists = 0,
    'ALTER TABLE usuarios ADD COLUMN codigo_supervisor_tg650 VARCHAR(20) NULL AFTER viu_novidades',
    'SELECT ''SKIP: coluna usuarios.codigo_supervisor_tg650 já existe'' AS info'
);

PREPARE stmt_add_col FROM @sql_add_col;
EXECUTE stmt_add_col;
DEALLOCATE PREPARE stmt_add_col;

-- 3) Garantir tabela supervisor_representante_vinculos
CREATE TABLE IF NOT EXISTS supervisor_representante_vinculos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    supervisor_id INT NOT NULL,
    codigo_representante VARCHAR(50) NOT NULL,
    nome_representante VARCHAR(200) NULL,
    ativo BOOLEAN DEFAULT TRUE,
    data_cadastro DATETIME DEFAULT CURRENT_TIMESTAMP,
    sincronizado_tg650 BOOLEAN DEFAULT FALSE,
    codigo_supervisor_tg650 VARCHAR(20) NULL,

    INDEX idx_supervisor_id (supervisor_id),
    INDEX idx_codigo_representante (codigo_representante),
    UNIQUE KEY uq_supervisor_representante (supervisor_id, codigo_representante),

    CONSTRAINT fk_supervisor_repr_supervisor
        FOREIGN KEY (supervisor_id) REFERENCES usuarios(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4) Verificações finais
SELECT DATABASE() AS banco_ativo;

SELECT COLUMN_TYPE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'usuarios'
  AND COLUMN_NAME = 'tipo';

SHOW COLUMNS FROM usuarios LIKE 'codigo_supervisor_tg650';
SHOW TABLES LIKE 'supervisor_representante_vinculos';

SELECT 'OK - Migration idempotente aplicada' AS status;
