-- Migration: Adicionar tipo de usuário supervisor_repr e tabela de vínculos
-- Data: 2026-04-01
-- Descrição: Implementa o tipo de usuário "Supervisor de Representante" com permissões somente leitura

-- 1. Alterar enum do campo tipo na tabela usuarios para incluir supervisor_repr
ALTER TABLE usuarios MODIFY COLUMN tipo ENUM('consultor', 'supervisor', 'televendas', 'supervisor_repr') DEFAULT 'consultor';

-- 2. Adicionar campo codigo_supervisor_tg650 na tabela usuarios
ALTER TABLE usuarios ADD COLUMN codigo_supervisor_tg650 VARCHAR(20) NULL AFTER viu_novidades;

-- 3. Criar tabela de vínculos supervisor-representante
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
    
    FOREIGN KEY (supervisor_id) REFERENCES usuarios(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. Adicionar comentários nas tabelas
ALTER TABLE usuarios COMMENT = 'Usuários do sistema - tipos: consultor, supervisor, televendas, supervisor_repr';
ALTER TABLE supervisor_representante_vinculos COMMENT = 'Vínculos entre supervisores de representante e códigos de representantes Oracle (TG 650)';
