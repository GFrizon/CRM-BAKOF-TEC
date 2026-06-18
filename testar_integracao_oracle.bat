@echo off
echo ========================================
echo 🧪 TESTE COMPLETO DA INTEGRACAO ORACLE
echo ========================================

cd /d "d:\AppMonitoramento\Monitoramento Clientes Bakof"

echo.
echo 1️⃣ Verificando ambiente virtual...
if exist ".venv\Scripts\activate.bat" (
    echo ✅ Ambiente virtual encontrado
    call .venv\Scripts\activate.bat
) else (
    echo ❌ Ambiente virtual não encontrado
    echo Execute primeiro: Instalar_APP.bat
    pause
    exit /b 1
)

echo.
echo 2️⃣ Verificando dependências...
pip show cx-oracle >nul 2>&1
if %errorlevel% equ 0 (
    echo ✅ cx-Oracle instalado
) else (
    echo ❌ cx-Oracle não encontrado
    echo Instalando...
    pip install cx-oracle==8.3.0
)

echo.
echo 3️⃣ Verificando variáveis de ambiente Oracle...
if not exist ".env" (
    echo ❌ Arquivo .env não encontrado
    echo Criando .env básico...
    echo DB_USER=root > .env
    echo DB_PASSWORD=ALTERE_A_SENHA_MYSQL >> .env
    echo DB_HOST=127.0.0.1 >> .env
    echo DB_PORT=3306 >> .env
    echo DB_NAME=controle_ligacoes >> .env
    echo SECRET_KEY=chave-secreta-temporaria >> .env
    echo.
    echo ⚠️ Configure as variáveis Oracle no .env:
    echo ORACLE_UID=BAKOF
    echo ORACLE_PWD=BAKOF
    echo ORACLE_DBQ=ORCL
    echo.
)

echo.
echo 4️⃣ Testando conexão Oracle...
python -c "
import sys
import os
sys.path.append('.')

print('🔍 Testando importação do módulo Oracle...')
try:
    from oracle_service import test_oracle_connection, get_clientes_oracle
    print('✅ Módulo Oracle importado com sucesso')
except Exception as e:
    print(f'❌ Erro ao importar módulo Oracle: {e}')
    sys.exit(1)

print('🔍 Testando conexão com banco Oracle...')
try:
    success, message = test_oracle_connection()
    if success:
        print(f'✅ Conexão Oracle funcionando!')
        print(f'   Mensagem: {message}')
    else:
        print(f'❌ Falha na conexão Oracle')
        print(f'   Erro: {message}')
        print('   Verifique:')
        print('   - Oracle está instalado e rodando?')
        print('   - Variáveis ORACLE_UID, ORACLE_PWD, ORACLE_DBQ estão corretas?')
        print('   - Rede permite conexão com o servidor Oracle?')
except Exception as e:
    print(f'❌ Erro ao testar conexão: {e}')
    sys.exit(1)

print('🔍 Testando query de clientes...')
try:
    clientes = get_clientes_oracle()
    print(f'✅ Query executada com sucesso!')
    print(f'   Total de clientes encontrados: {len(clientes)}')
    if len(clientes) > 0:
        print('   Exemplo de cliente:')
        print(f'   - Código: {clientes[0].get(\"cd_cliente\", \"N/A\")}')
        print(f'   - Nome: {clientes[0].get(\"cliente\", \"N/A\")}')
        print(f'   - Conceito: {clientes[0].get(\"conceito\", \"N/A\")}')
        print(f'   - Consultor: {clientes[0].get(\"consultor\", \"N/A\")}')
    else:
        print('⚠️ Nenhum cliente encontrado (pode ser normal se não houver dados no período)')
except Exception as e:
    print(f'❌ Erro ao executar query: {e}')
    print('   Verifique se a query SQL está correta para seu banco Oracle')
"

echo.
echo 5️⃣ Verificando campos Oracle no modelo Cliente...
python -c "
import sys
sys.path.append('.')

try:
    from app import app, Cliente
    with app.app_context():
        # Verificar se os campos Oracle existem no modelo
        campos_oracle = [
            'cd_cliente_oracle',
            'categoria_consultor', 
            'conceito',
            'ultimo_pedido_oracle',
            'valor_ultimo_pedido',
            'situacao_ultimo_pedido',
            'representante_oracle',
            'data_ultima_sincronizacao'
        ]
        
        print('🔍 Verificando campos Oracle no modelo Cliente...')
        for campo in campos_oracle:
            if hasattr(Cliente, campo):
                print(f'✅ Campo {campo} existe')
            else:
                print(f'❌ Campo {campo} não encontrado')
        
        print('✅ Modelo Cliente verificado')
except Exception as e:
    print(f'❌ Erro ao verificar modelo: {e}')
"

echo.
echo ========================================
echo 📊 RESUMO DO TESTE
echo ========================================
echo.
echo ✅ Para testar via navegador:
echo    1. Inicie o app: python app.py
echo    2. Acesse: http://localhost:5000
echo    3. Faça login como: supervisor@bakof.com.br / admin123
echo    4. Teste as rotas:
echo       - http://localhost:5000/test-oracle
echo       - http://localhost:5000/oracle-clientes-alvo
echo.
echo ⚠️ Se a conexão Oracle falhar:
echo    1. Verifique se o Oracle Client está instalado
echo    2. Configure as variáveis ORACLE_UID, ORACLE_PWD, ORACLE_DBQ no .env
echo    3. Verifique conectividade de rede com o servidor Oracle
echo    4. Confirme se o usuário BAKOF tem permissão no schema
echo.
echo 🚀 Para sincronizar dados:
echo    Use o botão "Sincronizar Oracle" (quando implementado na interface)
echo    Ou faça POST para: http://localhost:5000/sincronizar-oracle
echo.
pause
