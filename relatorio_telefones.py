#!/usr/bin/env python3
"""
Relatório de problemas de telefones e correção
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

def analisar_problemas_reais():
    """Analisa os problemas reais encontrados na base"""
    
    print("🔍 Análise Detalhada de Problemas de Telefones")
    print("=" * 60)
    
    with app.app_context():
        # Buscar clientes com problemas específicos
        query = """
            SELECT id, nome, telefone, telefone2, cd_cliente_oracle
            FROM clientes 
            WHERE cd_cliente_oracle IS NOT NULL 
            AND ativo = 1
            AND (
                telefone LIKE '(%) %' OR 
                telefone2 LIKE '(%) %' OR
                telefone IS NOT NULL OR
                telefone2 IS NOT NULL
            )
            ORDER BY nome
        """
        
        result = db.session.execute(query)
        clientes = result.fetchall()
        
        problemas = []
        correcoes = []
        
        for cliente in clientes:
            # Analisar telefone1
            if cliente.telefone:
                tipo1 = identificar_tipo_telefone(cliente.telefone)
                
                # Verificar problemas específicos
                if len(cliente.telefone.replace('(', '').replace(')', '').replace(' ', '').replace('-', '')) > 12:
                    problemas.append({
                        'id': cliente.id,
                        'nome': cliente.nome,
                        'campo': 'telefone1',
                        'valor': cliente.telefone,
                        'problema': 'Número muito longo',
                        'tipo': tipo1
                    })
                elif tipo1 == 'desconhecido':
                    problemas.append({
                        'id': cliente.id,
                        'nome': cliente.nome,
                        'campo': 'telefone1',
                        'valor': cliente.telefone,
                        'problema': 'Formato desconhecido',
                        'tipo': tipo1
                    })
            
            # Analisar telefone2
            if cliente.telefone2:
                tipo2 = identificar_tipo_telefone(cliente.telefone2)
                
                if len(cliente.telefone2.replace('(', '').replace(')', '').replace(' ', '').replace('-', '')) > 12:
                    problemas.append({
                        'id': cliente.id,
                        'nome': cliente.nome,
                        'campo': 'telefone2',
                        'valor': cliente.telefone2,
                        'problema': 'Número muito longo',
                        'tipo': tipo2
                    })
                elif tipo2 == 'desconhecido':
                    problemas.append({
                        'id': cliente.id,
                        'nome': cliente.nome,
                        'campo': 'telefone2',
                        'valor': cliente.telefone2,
                        'problema': 'Formato desconhecido',
                        'tipo': tipo2
                    })
        
        # Mostrar problemas encontrados
        if problemas:
            print(f"\n❌ {len(problemas)} problemas encontrados:")
            print("=" * 80)
            
            for i, problema in enumerate(problemas[:20], 1):  # Primeiros 20
                print(f"{i:2d}. ID {problema['id']:4d} | {problema['nome'][:40]}")
                print(f"     Campo: {problema['campo']} | Problema: {problema['problema']}")
                print(f"     Valor: '{problema['valor']}' | Tipo: {problema['tipo']}")
                print()
            
            if len(problemas) > 20:
                print(f"... e mais {len(problemas) - 20} problemas")
        else:
            print("\n✅ Nenhum problema encontrado!")
        
        # Estatísticas por tipo
        print(f"\n📊 Estatísticas:")
        print("=" * 30)
        print(f"   Total de problemas: {len(problemas)}")
        
        if problemas:
            por_problema = {}
            for p in problemas:
                por_problema[p['problema']] = por_problema.get(p['problema'], 0) + 1
            
            for problema, count in por_problema.items():
                print(f"   {problema}: {count}")
        
        # Sugerir correções
        print(f"\n💡 Sugestões de correção:")
        print("=" * 40)
        
        if problemas:
            print("1. Para números muito longos: remover dígitos extras")
            print("2. Para formatos desconhecidos: revisar regras de padronização")
            print("3. Considerar sincronização novamente com Oracle")
            print("4. Verificar se há dados corrompidos na base")
        else:
            print("✅ Todos os telefones estão padronizados corretamente!")

def mostrar_padroes_encontrados():
    """Mostra os padrões de telefones encontrados na base"""
    
    print("\n📋 Padrões de Telefones Encontrados:")
    print("=" * 50)
    
    with app.app_context():
        query = """
            SELECT DISTINCT 
                CASE 
                    WHEN telefone IS NOT NULL THEN SUBSTRING(telefone, 1, 8)
                    ELSE NULL
                END as padrao_tel1,
                CASE 
                    WHEN telefone2 IS NOT NULL THEN SUBSTRING(telefone2, 1, 8)
                    ELSE NULL
                END as padrao_tel2,
                COUNT(*) as quantidade
            FROM clientes 
            WHERE cd_cliente_oracle IS NOT NULL 
            AND ativo = 1
            AND (telefone IS NOT NULL OR telefone2 IS NOT NULL)
            GROUP BY padrao_tel1, padrao_tel2
            ORDER BY quantidade DESC
            LIMIT 15
        """
        
        result = db.session.execute(query)
        padroes = result.fetchall()
        
        for i, padrao in enumerate(padroes, 1):
            tel1 = padrao.padrao_tel1 or '[vazio]'
            tel2 = padrao.padrao_tel2 or '[vazio]'
            print(f"{i:2d}. Tel1: {tel1:<12} | Tel2: {tel2:<12} | Qtd: {padrao.quantidade}")

if __name__ == "__main__":
    analisar_problemas_reais()
    mostrar_padroes_encontrados()
    input("\nPressione Enter para sair...")
