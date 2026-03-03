# Script para adicionar ordenação por relevância no frontend
from app import app, db, Cliente
from flask import request

# Teste da ordenação por relevância
with app.app_context():
    print('🔍 Testando ordenação por relevância no frontend...')
    
    # Simular request com parâmetro de ordenação
    class MockRequest:
        def __init__(self):
            self.args = {'ordenar_por_relevancia': 'true'}
    
    # Substituir request temporariamente
    original_request = app.request_class
    app.request_class = lambda: MockRequest()
    
    try:
        # Testar query com ordenação por relevância
        q = Cliente.query.filter(
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ativo == True
        )
        
        # Ordenar por valor total dos últimos 365 dias
        clientes_relevantes = q.order_by(
            db.case([(Cliente.valor_total_365dias.is_(None), 0)], else_=Cliente.valor_total_365dias).desc(),
            Cliente.nome.asc()
        ).limit(10).all()
        
        print(f'✅ {len(clientes_relevantes)} clientes ordenados por relevância:')
        for i, cliente in enumerate(clientes_relevantes, 1):
            valor = float(cliente.valor_total_365dias or 0)
            print(f'  {i}. {cliente.nome} - R$ {valor:,.2f}')
            
    except Exception as e:
        print(f'❌ Erro: {e}')
    finally:
        # Restaurar request original
        app.request_class = original_request
