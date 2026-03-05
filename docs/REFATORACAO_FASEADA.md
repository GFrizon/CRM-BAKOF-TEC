# Plano de refatoracao faseada (producao)

## Objetivo
Organizar o sistema sem regressao funcional, com mudancas pequenas, validacao local e deploy controlado.

## Fase 1 (concluida nesta entrega)
- Extracao de configuracao para `config.py`.
- Extracao de extensoes Flask para `extensions.py`.
- Extracao de modelos para `models.py`.
- Extracao de helpers para `helpers.py`.
- Organizacao profissional em pastas com pacote `core/`:
  - `core/config.py`
  - `core/extensions.py`
  - `core/models.py`
  - `core/helpers.py`
- Extracao de rotas base para `routes/auth_routes.py`.
- Extracao dos handlers de erro para `routes/error_handlers.py`.
- Extracao do bootstrap/migracoes para `core/bootstrap_db.py`.
- Extracao do scheduler para `core/scheduler_runtime.py`.
- Extracao de rotas de conta/busca/remocao para `routes/account_client_routes.py`.
- Extracao completa das rotas Oracle para `routes/oracle_routes.py`.
- Extracao das rotas de supervisor para `routes/supervisor_routes.py`:
  - dashboard supervisor
  - ligacoes do dia
  - gerenciamento de usuarios
  - gerenciamento de banners
- Extracao de relatorio/admin:
  - `services/report_service.py` (HTML + envio de email)
  - `routes/admin_routes.py` (`/admin/enviar-relatorio`, `/admin/testar-scheduler`)
  - `core/scheduler_runtime.py` com `get_scheduler()`
- Extracao das rotas restantes de clientes/ligacoes/importacao/notas/apis para:
  - `routes/clientes_ligacoes_routes.py`
- Endurecimento do bootstrap:
  - `core/bootstrap_db.py` agora verifica existencia de coluna em `INFORMATION_SCHEMA`
  - evita `ALTER TABLE` repetitivo e reduz ruido de warning em startup
- Smoke test de regressao:
  - `scripts/smoke_test_routes.py` para validar rotas criticas sem erro 500
- Arquivos na raiz mantidos como wrappers de compatibilidade para reduzir risco.
- `app.py` mantido como ponto de entrada principal.
- Correcao de conflito de rota duplicada:
  - `POST /sincronizar-oracle` (rota oficial usada na UI)
  - `POST /sincronizar-oracle-async` (rota manual assincrona, antes duplicada/inacessivel)
- Correcao de origem da sincronizacao Oracle para valor valido do enum:
  - de `sincronizacao_oracle` para `importado_csv`.

## Fase 2 (proxima, recomendada)
- Extrair o bootstrap de banco/migracoes para modulo proprio (`bootstrap_db.py`).
- Trocar `try/except` de migracao por verificacao de schema antes de `ALTER TABLE`.
- Reduzir side effects no import do `app.py`.

## Fase 3
- Quebrar rotas por dominios em Blueprints:
  - `auth`, `clientes`, `ligacoes`, `supervisor`, `oracle`, `admin`.
- Manter URL/endpoint antigos durante transicao para nao quebrar frontend.

## Fase 4
- Criar camada de servicos:
  - `services/oracle_sync_service.py`
  - `services/relatorio_service.py`
  - `services/clientes_service.py`
- Remover logica de negocio pesada das rotas.

## Fase 5
- Adicionar testes de regressao (smoke + rotas criticas).
- Checklist de deploy com rollback e validacao pos-deploy.

## Regras de seguranca para cada fase
- Sempre deployar por etapa pequena.
- Validar no homolog com copia de banco antes de producao.
- Medir:
  - login
  - listagem de clientes
  - registro de ligacao
  - dashboard supervisor
  - sincronizacao Oracle
  - envio de relatorio
