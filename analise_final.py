#!/usr/bin/env python3
"""
Análise final do problema de padronização
"""

import os
import sys
from dotenv import load_dotenv

# Carregar .env
load_dotenv('.env')

# Adicionar diretório atual ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db

def analisar_contexto_telefones():
    """Analisa o contexto para entender os padrões reais"""
    
    print("🔍 Análise de Contexto - Por que não existe padrão único:")
    print("=" * 70)
    
    with app.app_context():
        # Analisar telefones por DDD para entender padrões locais
        query = """
            SELECT 
                SUBSTRING(telefone, 2, 2) as ddd,
                SUBSTRING(telefone, 6) as numero,
                LENGTH(SUBSTRING(telefone, 6)) as tamanho,
                COUNT(*) as quantidade
            FROM clientes 
            WHERE cd_cliente_oracle IS NOT NULL 
            AND ativo = 1
            AND telefone IS NOT NULL
            AND telefone LIKE '(%'
            GROUP BY ddd, numero, tamanho
            ORDER BY ddd, quantidade DESC
        """
        
        result = db.session.execute(query)
        dados = result.fetchall()
        
        # Agrupar por DDD
        por_ddd = {}
        for item in dados:
            ddd = item.ddd
            if ddd not in por_ddd:
                por_ddd[ddd] = []
            por_ddd[ddd].append(item)
        
        print("\n📊 Análise por DDD:")
        print("=" * 50)
        
        for ddd, itens in sorted(por_ddd.items()):
            print(f"\nDDD {ddd}:")
            
            # Contar por tamanho
            tamanhos = {}
            for item in itens:
                tamanho = item.tamanho
                tamanhos[tamanho] = tamanhos.get(tamanho, 0) + item.quantidade
            
            for tamanho, qtd in sorted(tamanhos.items()):
                print(f"   {tamanho} dígitos: {qtd} ocorrências")
            
            # Mostrar exemplos
            print(f"   Exemplos:")
            for i, item in enumerate(itens[:3]):
                prefixo = item.numero[:3]
                print(f"     ({ddd}) {prefixo}... ({item.quantidade}x)")

def explicar_problema():
    """Explica por que não existe padrão único"""
    
    print("\n\n🎯 O PROBLEMA REAL: Não existe padrão único no Brasil!")
    print("=" * 60)
    
    print("\n📋 Fatos sobre telefonia brasileira:")
    print("   • Celulares: 9 dígitos (começam com 9, 6, 7, 8)")
    print("   • Fixos: 8 dígitos (começam com 2, 3, 4, 5)")
    print("   • Exceções: algumas regiões têm padrões diferentes")
    print("   • Histórico: celulares antigos tinham 8 dígitos")
    
    print("\n❓ Por que os números parecem 'errados':")
    print("   • (51) 492255780 → Pode ser fixo com 9 dígitos (raro)")
    print("   • (51) 555481345 → Pode ser celular antigo ou erro")
    print("   • Não dá para saber 100% sem contexto local")
    
    print("\n💡 SOLUÇÃO PRÁTICA:")
    print("   1. Manter formato atual para não quebrar ligações")
    print("   2. Adicionar opção de correção manual")
    print("   3. Usar heurística baseada em DDD local")
    print("   4. Priorizar funcionamento do MicroSIP")

def sugerir_abordagem():
    """Sugere abordagem prática"""
    
    print("\n\n🛠️ ABORDAGEM PRÁTICA RECOMENDADA:")
    print("=" * 50)
    
    print("\n1️⃣ **Manter formatação atual para números que funcionam**")
    print("   • Se o MicroSIP consegue ligar, não mexer")
    print("   • Priorizar funcionamento sobre 'padrão teórico'")
    
    print("\n2️⃣ **Corrigir apenas casos claros**")
    print("   • Celulares de 8 dígitos → Adicionar 9")
    print("   • Formatos obviamente errados → Corrigir")
    print("   • Manter casos ambíguos como estão")
    
    print("\n3️⃣ **Criar ferramenta de correção manual**")
    print("   • Interface para ajustar telefones problemáticos")
    print("   • Log de alterações para auditoria")
    print("   • Validação antes de aplicar")
    
    print("\n4️⃣ **Melhorar detecção automática**")
    print("   • Basear em DDD específico")
    print("   • Usar estatísticas da base")
    print("   • Aprender com correções manuais")

if __name__ == "__main__":
    analisar_contexto_telefones()
    explicar_problema()
    sugerir_abordagem()
    input("\nPressione Enter para sair...")
