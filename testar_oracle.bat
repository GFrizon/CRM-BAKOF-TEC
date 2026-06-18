@echo off
echo ========================================
echo Instalando dependencias Oracle...
echo ========================================

cd /d "d:\AppMonitoramento\Monitoramento Clientes Bakof"

echo Ativando ambiente virtual...
call .venv\Scripts\activate.bat

echo Instalando cx-Oracle...
pip install cx-oracle==8.3.0

echo ========================================
echo Testando conexao Oracle...
echo ========================================

python -c "
import sys
sys.path.append('.')
try:
    from oracle_service import test_oracle_connection
    success, message = test_oracle_connection()
    print(f'Resultado: {success}')
    print(f'Mensagem: {message}')
    if success:
        print('✅ Conexao Oracle funcionando!')
    else:
        print('❌ Erro na conexao Oracle')
except Exception as e:
    print(f'Erro ao importar/testar: {str(e)}')
"

echo ========================================
echo Teste concluido!
echo ========================================
pause
