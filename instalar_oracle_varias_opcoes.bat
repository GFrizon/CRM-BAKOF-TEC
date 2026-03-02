@echo off
echo ========================================
echo 🔧 INSTALAÇÃO ORACLE - VÁRIAS OPÇÕES
echo ========================================

cd /d "d:\AppMonitoramento\Monitoramento Clientes Bakof"

echo.
echo 1️⃣ Ativando ambiente virtual...
call .venv\Scripts\activate.bat

echo.
echo 2️⃣ Tentando instalar oracledb (versão mais recente)...
pip install oracledb --upgrade

if %errorlevel% neq 0 (
    echo.
    echo ❌ Falha ao instalar oracledb
    
    echo.
    echo 3️⃣ Tentando cx-oracle (alternativa)...
    pip install cx-oracle==8.3.0
    
    if %errorlevel% neq 0 (
        echo.
        echo ❌ Falha ao instalar cx-oracle também
        
        echo.
        echo 4️⃣ Tentando versão wheel pré-compilada...
        pip install --only-binary=all oracledb
        
        if %errorlevel% neq 0 (
            echo.
            echo ❌ Todas as tentativas falharam
            echo.
            echo 🔧 SOLUÇÕES POSSÍVEIS:
            echo 1. Instale Microsoft Visual C++ Build Tools:
            echo    https://visualstudio.microsoft.com/visual-cpp-build-tools/
            echo.
            echo 2. Use Python 3.11 ou 3.12 em vez de 3.13
            echo.
            echo 3. Instale Oracle Client e use cx-oracle
            echo.
            pause
            exit /b 1
        )
    )
)

echo.
echo 5️⃣ Testando importação...
python -c "
try:
    import oracledb
    print('✅ oracledb importado com sucesso')
    print(f'   Versão: {oracledb.__version__}')
    ORACLE_OK = True
except ImportError:
    try:
        import cx_Oracle
        print('✅ cx_Oracle importado com sucesso')
        ORACLE_OK = True
    except ImportError:
        print('❌ Nenhuma biblioteca Oracle funcionando')
        ORACLE_OK = False

if ORACLE_OK:
    try:
        from oracle_service_alternativo import test_oracle_connection
        print('✅ oracle_service_alternativo importado')
    except ImportError as e:
        print(f'❌ Erro ao importar serviço: {e}')
"

echo.
echo 6️⃣ Configurando variáveis ambiente (se necessário)...
if not exist ".env" (
    echo Criando .env básico com configurações Oracle...
    echo DB_USER=root > .env
    echo DB_PASSWORD=1235 >> .env
    echo DB_HOST=127.0.0.1 >> .env
    echo DB_PORT=3306 >> .env
    echo DB_NAME=controle_ligacoes >> .env
    echo SECRET_KEY=chave-secreta-temporaria >> .env
    echo ORACLE_UID=BAKOF >> .env
    echo ORACLE_PWD=BAKOF >> .env
    echo ORACLE_DBQ=ORCL >> .env
    echo ✅ .env criado com variáveis Oracle
) else (
    echo ✅ .env já existe
)

echo.
echo ========================================
echo ✅ INSTALAÇÃO CONCLUÍDA!
echo ========================================
echo.
echo Agora execute o teste:
echo python testar_oracle_simples.py
echo.
echo Ou use o serviço alternativo:
echo python -c "from oracle_service_alternativo import test_oracle_connection; print(test_oracle_connection())"
echo.
pause
