# Sistema de Controle de Ligações

Aplicação Flask para gestão de clientes, ligações, dashboard de supervisão, relatórios por e-mail e integração Oracle.

## Requisitos
- Python 3.10+
- MySQL
- Dependências em `requirements.txt`

## Instalação
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Configuração
Crie/ajuste o arquivo `.env` na raiz.

Variáveis principais:
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `DB_NAME`
- `SECRET_KEY`
- `MAIL_SERVER`
- `MAIL_PORT`
- `MAIL_USERNAME`
- `MAIL_PASSWORD`
- `MAIL_RECIPIENTS`
- `ORACLE_UID`
- `ORACLE_PWD`
- `ORACLE_DBQ`

## Executar
```bash
python app.py
```

URL padrão:
- `http://localhost:5000`

Também é possível iniciar por:
- `INICIAR.bat`

## Estrutura Atual
```text
app.py                     # Entrypoint
core/
  config.py               # Configuração e env
  extensions.py           # db/mail/login_manager
  models.py               # Modelos SQLAlchemy
  helpers.py              # Funções utilitárias
  bootstrap_db.py         # Bootstrap/migrações simples
  scheduler_runtime.py    # Scheduler diário
routes/
  auth_routes.py
  account_client_routes.py
  clientes_ligacoes_routes.py
  supervisor_routes.py
  oracle_routes.py
  admin_routes.py
  error_handlers.py
services/
  report_service.py       # Montagem/Envio de relatório
templates/
static/
```

## Observações de Produção
- O app sobe com Waitress (`app.py`).
- O scheduler agenda:
  - relatório diário
  - sincronização Oracle diária
- O bootstrap de banco executa no startup e já evita `ALTER TABLE` redundante.

## Smoke Check Rápido
Depois de alterações:
```bash
python -m py_compile app.py
python app.py
```

## Troubleshooting
- Erro MySQL: revisar variáveis `DB_*` no `.env`.
- Erro Oracle (`DPY-*`): revisar `ORACLE_*`, client/driver e rede.
- Erro de e-mail: revisar `MAIL_*` e credenciais SMTP.
