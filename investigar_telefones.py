#!/usr/bin/env python3
"""
Investigar problemas de padronização de telefones
"""

import os
import sys
from dotenv import load_dotenv

# Carregar .env
load_dotenv('.env')

# Adicionar diretório atual ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from telefone_utils import padronizar_telefone, identificar_tipo_telefone

def investigar_problemas():
    """Investiga telefones que podem estar com problemas"""
    
    print("🔍 Investigando problemas de padronização...")
    
    with app.app_context():
        # Buscar alguns clientes para analisar
        query = """
            SELECT id, nome, telefone, telefone2, cd_cliente_oracle
            FROM clientes 
            WHERE cd_cliente_oracle IS NOT NULL 
            AND ativo = 1
            ORDER BY RAND()
            LIMIT 20
        """
        
        result = db.session.execute(query)
        clientes = result.fetchall()
        
        print(f"\n📋 Análise de {len(clientes)} clientes:")
        print("=" * 80)
        
        problemas = []
        
        for cliente in clientes:
            print(f"\n📞 Cliente: {cliente.nome[:50]}")
            print(f"   ID: {cliente.id} | Oracle: {cliente.cd_cliente_oracle}")
            
            # Analisar telefone1
            tel1 = cliente.telefone
            if tel1:
                tipo1 = identificar_tipo_telefone(tel1)
                print(f"   Telefone1: '{tel1}' ({tipo1})")
                
                # Verificar problemas comuns
                if tel1.startswith('(0') or tel1.startswith('(00'):
                    problemas.append(f"ID {cliente.id}: Telefone1 com zero extra: {tel1}")
                elif '(' not in tel1 or ')' not in tel1:
                    problemas.append(f"ID {cliente.id}: Telefone1 sem formatação: {tel1}")
                elif len(tel1) < 10:
                    problemas.append(f"ID {cliente.id}: Telefone1 muito curto: {tel1}")
            else:
                print(f"   Telefone1: [vazio]")
            
            # Analisar telefone2
            tel2 = cliente.telefone2
            if tel2:
                tipo2 = identificar_tipo_telefone(tel2)
                print(f"   Telefone2: '{tel2}' ({tipo2})")
                
                # Verificar problemas comuns
                if tel2.startswith('(0') or tel2.startswith('(00'):
                    problemas.append(f"ID {cliente.id}: Telefone2 com zero extra: {tel2}")
                elif '(' not in tel2 or ')' not in tel2:
                    problemas.append(f"ID {cliente.id}: Telefone2 sem formatação: {tel2}")
                elif len(tel2) < 10:
                    problemas.append(f"ID {cliente.id}: Telefone2 muito curto: {tel2}")
            else:
                print(f"   Telefone2: [vazio]")
        
        # Mostrar resumo de problemas
        if problemas:
            print(f"\n❌ Problemas encontrados ({len(problemas)}):")
            print("=" * 50)
            for problema in problemas[:10]:  # Primeiros 10 problemas
                print(f"   • {problema}")
            
            if len(problemas) > 10:
                print(f"   ... e mais {len(problemas) - 10} problemas")
        else:
            print(f"\n✅ Nenhum problema encontrado nos clientes analisados")
        
        # Estatísticas gerais
        query_stats = """
            SELECT 
                COUNT(*) as total_clientes,
                COUNT(telefone) as com_telefone1,
                COUNT(telefone2) as com_telefone2,
                COUNT(CASE WHEN telefone IS NOT NULL AND telefone2 IS NOT NULL THEN 1 END) as com_dois_telefones
            FROM clientes 
            WHERE cd_cliente_oracle IS NOT NULL 
            AND ativo = 1
        """
        
        result_stats = db.session.execute(query_stats)
        stats = result_stats.fetchone()
        
        print(f"\n📊 Estatísticas gerais:")
        print("=" * 30)
        print(f"   Total clientes: {stats.total_clientes}")
        print(f"   Com telefone1: {stats.com_telefone1}")
        print(f"   Com telefone2: {stats.com_telefone2}")
        print(f"   Com dois telefones: {stats.com_dois_telefones}")

def testar_padroes_conhecidos():
    """Testa diferentes padrões que podem existir"""
    
    print("\n🧪 Testando padrões conhecidos:")
    print("=" * 50)
    
    # Padrões problemáticos que podem existir
    testes = [
        # Com zeros extras
        "(055) 996203010",
        "(0055) 996203010",
        "055 996203010",
        "0055 996203010",
        
        # Sem formatação
        "55996203010",
        "5599620301",
        "996203010",
        
        # Formatos estranhos
        "(55) 99620-3010",
        "55-99620-3010",
        "55.99620.3010",
        "55 99620 3010",
        
        # Possíveis erros
        "(55) 9962030100",  # 10 dígitos no número
        "(55) 99620301",    # 8 dígitos no número
        "(55) 9962030",     # 7 dígitos no número
    ]
    
    for teste in testes:
        resultado = padronizar_telefone(teste, "55")
        tipo = identificar_tipo_telefone(resultado) if resultado else 'inválido'
        
        status = "✅" if resultado else "❌"
        print(f"{status} '{teste}' → '{resultado}' ({tipo})")

if __name__ == "__main__":
    investigar_problemas()
    testar_padroes_conhecidos()
    input("\nPressione Enter para sair...")
