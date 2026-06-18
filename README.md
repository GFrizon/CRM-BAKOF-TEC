\# 🎯 Sistema de Controle de Ligações



Sistema completo para gerenciar ligações com clientes especiais, desenvolvido em Python + Flask.



\## 📋 Pré-requisitos



\- Python 3.8 ou superior

\- MySQL 5.7 ou superior

\- pip (gerenciador de pacotes Python)



\## 🚀 Instalação Passo a Passo



\### 1. Criar o Banco de Dados



Execute o arquivo SQL fornecido no seu MySQL:



```bash

mysql -u root -p < create\_database.sql

```



Ou execute manualmente as queries SQL no MySQL Workbench/phpMyAdmin.



\### 2. Estrutura do Projeto



Crie a seguinte estrutura de pastas:



```

controle-ligacoes/

├── app.py

├── models.py

├── requirements.txt

├── templates/

│   ├── login.html

│   ├── clientes.html

│   ├── importar.html

│   ├── supervisor.html

│   └── todos\_clientes.html

└── uploads/ (será criada automaticamente)

```



\### 3. Instalar Dependências



```bash

\# Navegue até a pasta do projeto

cd controle-ligacoes



\# Instale as dependências

pip install -r requirements.txt

```



\### 4. Configurar o Banco de Dados



Edite o arquivo `app.py` na linha 11:



```python

app.config\['SQLALCHEMY\_DATABASE\_URI'] = 'mysql+pymysql://SEU\_USUARIO:SUA\_SENHA@localhost/controle\_ligacoes'

```



Substitua:

\- `SEU\_USUARIO` pelo seu usuário MySQL (geralmente `root`)

\- `SUA\_SENHA` pela senha do MySQL



\### 5. Executar o Sistema



```bash

python app.py

```



O sistema estará disponível em: \*\*http://localhost:5000\*\*



\## 👤 Usuários Padrão



\### Consultor

\- \*\*Email:\*\* gabriel@empresa.com

\- \*\*Senha:\*\* 123456



\### Supervisor

\- \*\*Email:\*\* supervisor@empresa.com

\- \*\*Senha:\*\* admin123



\## 📊 Funcionalidades



\### Para Consultores:

✅ Importar clientes via CSV  

✅ Visualizar apenas seus clientes  

✅ Registrar ligações com observações  

✅ Ver histórico de ligações por cliente  

✅ Filtros e buscas  



\### Para Supervisores:

✅ Dashboard com estatísticas gerais  

✅ Gráficos de desempenho  

✅ Ranking de consultores  

✅ Visualizar todos os clientes  

✅ Histórico completo de ligações  



\## 📄 Formato do CSV para Importação



Crie um arquivo CSV com as seguintes colunas:



```csv

nome,cnpj,telefone,email,endereco

"Empresa ABC LTDA","12.345.678/0001-90","(55) 3744-1234","contato@abc.com","Rua Exemplo 123"

"Empresa XYZ SA","98.765.432/0001-10","(55) 3744-5678","info@xyz.com","Av Principal 456"

```



\*\*Colunas obrigatórias:\*\* nome, cnpj, telefone  

\*\*Colunas opcionais:\*\* email, endereco



\## 🌐 Rodando na Rede Local



Para acessar de outros computadores da rede:



1\. Descubra seu IP local:

&nbsp;  - Windows: `ipconfig`

&nbsp;  - Linux/Mac: `ifconfig` ou `ip addr`



2\. Outros computadores acessam via: `http://SEU\_IP:5000`



3\. Configure o firewall para permitir conexões na porta 5000



\## 🔒 Segurança



\*\*IMPORTANTE:\*\* Antes de usar em produção:



1\. Mude a SECRET\_KEY no arquivo `app.py`:

```python

app.config\['SECRET\_KEY'] = 'sua-chave-super-secreta-aleatoria-aqui'

```



2\. Desative o modo debug:

```python

app.run(host='0.0.0.0', port=5000, debug=False)

```



3\. Use um servidor WSGI (gunicorn) em vez de rodar direto pelo Flask



\## 🆘 Problemas Comuns



\### Erro: "No module named flask"

```bash

pip install -r requirements.txt

```



\### Erro: "Access denied for user"

Verifique usuário e senha do MySQL no `app.py`



\### Erro: "Can't connect to MySQL server"

Certifique-se que o MySQL está rodando:

```bash

\# Windows

net start MySQL



\# Linux

sudo systemctl start mysql

```



\### CSV não importa

\- Verifique se o arquivo está em UTF-8

\- Confirme que as colunas obrigatórias existem

\- Veja se não há caracteres especiais no nome do arquivo



\## 📈 Próximas Melhorias



\- \[ ] Integração com Cigam

\- \[ ] Agendamento de ligações

\- \[ ] Notificações por email

\- \[ ] Relatórios em PDF

\- \[ ] App mobile



\## 💡 Dicas



1\. \*\*Backup automático:\*\* Configure um cron/task para backup do MySQL

2\. \*\*Performance:\*\* Se tiver muitos clientes, adicione índices no banco

3\. \*\*Logs:\*\* O Flask guarda logs no terminal, útil para debug



\## 🐛 Suporte



Para problemas ou dúvidas, verifique:

1\. Logs no terminal onde rodou `python app.py`

2\. Logs do MySQL

3\. Console do navegador (F12)



---



\*\*Desenvolvido com Python + Flask + MySQL + Bootstrap\*\*

