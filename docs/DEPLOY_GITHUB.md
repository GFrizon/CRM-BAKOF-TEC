# Atualização pelo GitHub

O código fica no GitHub. Credenciais, banco, snapshots, caches, logs e uploads
permanecem somente em cada máquina.

## Primeira instalação no servidor

```powershell
git clone URL_DO_REPOSITORIO app-monitoramento
cd app-monitoramento
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edite o `.env` do servidor com as credenciais reais. Nunca faça commit desse
arquivo.

Antes de iniciar:

```powershell
python -m compileall -q app.py core routes services
python -m unittest discover -s tests -v
```

## Publicar uma atualização local

```powershell
git status
git add -A
git commit -m "Descrição da atualização"
git push origin main
```

Revise sempre `git status` antes do commit. Arquivos `.env`, dados, caches,
logs, uploads e backups não devem aparecer.

## Atualizar o servidor

Faça backup do banco e confirme que o `.env` está presente. Depois:

```powershell
git status
git pull --ff-only origin main
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m compileall -q app.py core routes services
python -m unittest discover -s tests -v
```

Reinicie o serviço do aplicativo somente após as validações passarem.

Não use `git reset --hard` no servidor: isso pode apagar ajustes locais que
ainda não foram identificados.
