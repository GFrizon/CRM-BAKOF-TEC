#!/usr/bin/env python3
"""
Script para resetar o campo viu_novidades para todos os usuários ativos
Isso fará com que todos os usuários vejam o modal de novidades na próxima vez que acessarem o sistema
"""

import sys
import os

# Adicionar o diretório raiz ao path para importar o app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from core.models import Usuario

def resetar_novidades():
    """Resetar o campo viu_novidades para todos os usuários ativos"""
    
    with app.app_context():
        try:
            # Buscar todos os usuários ativos (assumindo que usuários ativos têm algum critério)
            usuarios = Usuario.query.all()
            
            print(f"Encontrados {len(usuarios)} usuários no banco de dados.")
            
            # Resetar o campo viu_novidades para False
            count = 0
            for usuario in usuarios:
                if usuario.viu_novidades:  # Só atualizar se já tiver visto
                    usuario.viu_novidades = False
                    count += 1
            
            # Commit das alterações
            db.session.commit()
            
            print(f"Campo viu_novidades resetado para {count} usuários.")
            print("Todos os usuários verão o modal de novidades na próxima vez que acessarem o sistema.")
            
        except Exception as e:
            print(f"Erro ao resetar novidades: {str(e)}")
            db.session.rollback()
            return False
        
        return True

if __name__ == "__main__":
    print("=== Resetar campo viu_novidades ===")
    sucesso = resetar_novidades()
    
    if sucesso:
        print("\nOperação concluída com sucesso!")
    else:
        print("\nOcorreu um erro durante a operação.")
        sys.exit(1)
