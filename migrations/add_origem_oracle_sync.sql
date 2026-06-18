-- Adiciona valor `oracle_sync` ao ENUM de `clientes.origem`.
-- Seguro para ambientes em que o valor ja exista.
ALTER TABLE clientes
MODIFY COLUMN origem ENUM('importado_csv','manual','oracle_sync')
NOT NULL DEFAULT 'manual';
