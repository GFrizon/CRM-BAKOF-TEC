@echo off
echo ========================================
echo 🔧 CORRIGINDO INSTALAÇÃO ORACLE
echo ========================================

cd /d "d:\AppMonitoramento\Monitoramento Clientes Bakof"

echo.
echo 1️⃣ Ativando ambiente virtual...
call .venv\Scripts\activate.bat

echo.
echo 2️⃣ Instalando oracledb (compatível com Python 3.13)...
pip install oracledb==2.1.0

echo.
echo 3️⃣ Atualizando pip...
python -m pip install --upgrade pip

echo.
echo 4️⃣ Instalando dependências restantes...
pip install -r requirements.txt

echo.
echo 5️⃣ Testando importação...
python -c "
try:
    import oracledb
    print('✅ oracledb importado com sucesso')
    print(f'   Versão: {oracledb.__version__}')
except ImportError as e:
    print(f'❌ Erro ao importar oracledb: {e}')
    exit(1)

try:
    from oracle_service import test_oracle_connection
    print('✅ oracle_service importado com sucesso')
except ImportError as e:
    print(f'❌ Erro ao importar oracle_service: {e}')
    exit(1)

print('✅ Todos os módulos Oracle importados!')
"

echo.
echo ========================================
echo ✅ INSTALAÇÃO CORRIGIDA!
echo ========================================
echo.
echo Agora configure as variáveis Oracle no .env:
echo ORACLE_UID=BAKOF
echo ORACLE_PWD=BAKOF
echo ORACLE_DBQ=ORCL
echo.
echo Depois execute: python testar_oracle_simples.py
echo.
pause
