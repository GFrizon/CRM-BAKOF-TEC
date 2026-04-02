"""
Script para aplicar migration do Supervisor de Representante
Executa as alterações necessárias no banco de dados MySQL

Uso:
    python apply_migration.py
"""

import os
import sys
from dotenv import load_dotenv
import pymysql

# Carregar variáveis de ambiente
load_dotenv()

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', 3306))
DB_USER = os.getenv('DB_USER', 'root')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_NAME = os.getenv('DB_NAME', 'controle_ligacoes')

def aplicar_migration():
    """Aplica a migration add_supervisor_repr.sql"""
    
    print("=" * 60)
    print("MIGRATION: Supervisor de Representante")
    print("=" * 60)
    print(f"Banco: {DB_NAME}@{DB_HOST}:{DB_PORT}")
    print(f"Usuário: {DB_USER}")
    print()
    
    # Ler o arquivo SQL
    migration_path = os.path.join(os.path.dirname(__file__), 'migrations', 'add_supervisor_repr.sql')
    
    if not os.path.exists(migration_path):
        print(f"❌ ERRO: Arquivo de migration não encontrado: {migration_path}")
        return False
    
    with open(migration_path, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    
    # Separar comandos SQL (dividir por ponto e vírgula)
    sql_commands = [cmd.strip() for cmd in sql_content.split(';') if cmd.strip() and not cmd.strip().startswith('--')]
    
    print(f"📄 Lido arquivo: {migration_path}")
    print(f"📋 Total de comandos SQL: {len(sql_commands)}")
    print()
    
    try:
        # Conectar ao banco
        print("🔌 Conectando ao banco de dados...")
        connection = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset='utf8mb4'
        )
        
        print("✅ Conectado com sucesso!")
        print()
        
        cursor = connection.cursor()
        
        # Executar cada comando
        for i, command in enumerate(sql_commands, 1):
            # Pular comentários
            if command.startswith('--'):
                continue
            
            print(f"[{i}/{len(sql_commands)}] Executando comando...")
            
            # Mostrar preview do comando (primeiras 80 caracteres)
            preview = command[:80].replace('\n', ' ')
            if len(command) > 80:
                preview += '...'
            print(f"    {preview}")
            
            try:
                cursor.execute(command)
                print(f"    ✅ OK")
            except pymysql.err.OperationalError as e:
                # Verificar se é erro de coluna já existente (pode ignorar)
                if 'Duplicate column name' in str(e):
                    print(f"    ⚠️  Coluna já existe (ignorando)")
                elif 'Table' in str(e) and 'already exists' in str(e):
                    print(f"    ⚠️  Tabela já existe (ignorando)")
                else:
                    print(f"    ❌ ERRO: {e}")
                    raise
            except Exception as e:
                print(f"    ❌ ERRO: {e}")
                raise
            
            print()
        
        # Commit das alterações
        connection.commit()
        print("💾 Commit realizado com sucesso!")
        print()
        
        # Verificar estrutura criada
        print("🔍 Verificando estrutura criada...")
        
        # Verificar coluna codigo_supervisor_tg650
        cursor.execute("SHOW COLUMNS FROM usuarios LIKE 'codigo_supervisor_tg650'")
        if cursor.fetchone():
            print("    ✅ Coluna 'codigo_supervisor_tg650' criada")
        else:
            print("    ❌ Coluna 'codigo_supervisor_tg650' NÃO encontrada")
        
        # Verificar enum tipo
        cursor.execute("SHOW COLUMNS FROM usuarios WHERE Field = 'tipo'")
        tipo_info = cursor.fetchone()
        if tipo_info and 'supervisor_repr' in str(tipo_info):
            print("    ✅ Tipo 'supervisor_repr' adicionado ao enum")
        else:
            print("    ❌ Tipo 'supervisor_repr' NÃO encontrado no enum")
        
        # Verificar tabela supervisor_representante_vinculos
        cursor.execute("SHOW TABLES LIKE 'supervisor_representante_vinculos'")
        if cursor.fetchone():
            print("    ✅ Tabela 'supervisor_representante_vinculos' criada")
        else:
            print("    ❌ Tabela 'supervisor_representante_vinculos' NÃO encontrada")
        
        print()
        print("=" * 60)
        print("✅ MIGRATION APLICADA COM SUCESSO!")
        print("=" * 60)
        print()
        print("Próximos passos:")
        print("1. Reinicie a aplicação Flask")
        print("2. Acesse /supervisor/supervisores-representante")
        print("3. Crie um usuário supervisor_repr de teste")
        print()
        
        cursor.close()
        connection.close()
        
        return True
        
    except pymysql.err.OperationalError as e:
        print()
        print("=" * 60)
        print("❌ ERRO DE CONEXÃO")
        print("=" * 60)
        print(f"Erro: {e}")
        print()
        print("Verifique:")
        print(f"  - Host: {DB_HOST}")
        print(f"  - Porta: {DB_PORT}")
        print(f"  - Usuário: {DB_USER}")
        print(f"  - Banco: {DB_NAME}")
        print(f"  - Senha está correta no .env")
        print()
        return False
        
    except Exception as e:
        print()
        print("=" * 60)
        print("❌ ERRO AO APLICAR MIGRATION")
        print("=" * 60)
        print(f"Erro: {e}")
        print()
        print("A migration foi revertida (rollback automático)")
        print()
        return False

if __name__ == '__main__':
    print()
    confirmacao = input("⚠️  ATENÇÃO: Esta migration irá alterar a estrutura do banco de dados.\n   Deseja continuar? (s/N): ")
    
    if confirmacao.lower() not in ('s', 'sim', 'y', 'yes'):
        print("\n❌ Migration cancelada pelo usuário.")
        sys.exit(0)
    
    print()
    sucesso = aplicar_migration()
    
    if sucesso:
        sys.exit(0)
    else:
        sys.exit(1)
