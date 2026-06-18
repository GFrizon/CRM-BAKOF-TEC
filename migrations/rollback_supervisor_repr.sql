-- Rollback Migration: Remover tipo de usuário supervisor_repr e tabela de vínculos
-- Data: 2026-04-01
-- Descrição: Reverte a implementação do tipo de usuário "Supervisor de Representante"

-- ATENÇÃO: Este script remove dados! Execute apenas se necessário.

-- 1. Remover tabela de vínculos
DROP TABLE IF EXISTS supervisor_representante_vinculos;

-- 2. Remover campo codigo_supervisor_tg650 da tabela usuarios
ALTER TABLE usuarios DROP COLUMN IF EXISTS codigo_supervisor_tg650;

-- 3. Reverter enum do campo tipo (remover supervisor_repr)
-- ATENÇÃO: Certifique-se de que não há usuários com tipo = 'supervisor_repr' antes de executar
ALTER TABLE usuarios MODIFY COLUMN tipo ENUM('consultor', 'supervisor', 'televendas') DEFAULT 'consultor';
