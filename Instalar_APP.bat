@echo off
echo ========================================
echo   CONFIGURANDO AMBIENTE DO APLICATIVO
echo ========================================
echo.

:: 1) Criar ambiente virtual (.venv)
echo [1/4] Criando ambiente virtual...
python -m venv .venv

:: 2) Ativar ambiente virtual
echo [2/4] Ativando ambiente virtual...
call .venv\Scripts\activate

:: 3) Atualizar PIP
echo [3/4] Atualizando pip...
python -m pip install --upgrade pip

:: 4) Instalar dependencias do requirements.txt
echo [4/4] Instalando bibliotecas do requirements.txt...
pip install -r requirements.txt

echo.
echo ✅ Ambiente configurado com sucesso!
echo ✅ Agora você pode iniciar o app com:
echo    call .venv\Scripts\activate
echo    python app.py
echo.
pause
