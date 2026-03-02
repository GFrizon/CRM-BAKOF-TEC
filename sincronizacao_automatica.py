"""
Scheduler para sincronização automática diária com Oracle
"""

import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Carregar .env
load_dotenv('.env')

# Adicionar diretório atual ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def sincronizacao_automatica_diaria():
    """Sincronização automática diária com Oracle"""
    
    print("=" * 60)
    print("🔄 SINCRONIZAÇÃO AUTOMÁTICA DIÁRIA ORACLE")
    print("=" * 60)
    print(f"📅 Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    
    try:
        from app import app, db, Cliente, Usuario
        from oracle_service import get_clientes_oracle
        
        with app.app_context():
            print("\n🔍 1. Buscando clientes alvo no Oracle...")
            clientes_oracle = get_clientes_oracle()
            print(f"✅ {len(clientes_oracle)} clientes alvo encontrados no Oracle")
            
            print("\n📊 2. Analisando clientes atuais no MySQL...")
            todos_clientes_mysql = Cliente.query.filter_by(ativo=True).all()
            print(f"✅ {len(todos_clientes_mysql)} clientes totais no MySQL")
            
            # Separar clientes por origem
            clientes_oracle_mysql = Cliente.query.filter(
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ativo == True
            ).all()
            print(f"✅ {len(clientes_oracle_mysql)} clientes vindos do Oracle")
            
            print("\n👥 3. Verificando consultores disponíveis...")
            consultores = Usuario.query.filter_by(tipo='consultor', ativo=True).all()
            print(f"✅ {len(consultores)} consultores disponíveis")
            
            # Criar mapa de códigos Oracle para consultores
            mapa_consultores = {
                '100': 24,  # ROSELEIA → Roseleia Basso
                '002': 23,  # RODRIGO → Rodrigo Crespan
                '012': 25,  # SANDRA → Sandra Vendruscolo da Silva
                '001': 19,  # Elisabete Haus
                '003': 20,  # Janine de Mello
                '004': 21,  # Iara Sponchiado
                '005': 22,  # Carla Siduoski
                '006': 26,  # Sibele Froner
            }
            
            print("\n🔄 4. Processando sincronização...")
            
            # Conjuntos para controle
            codigos_oracle_atuais = {str(c.get('cd_cliente', '')) for c in clientes_oracle}
            codigos_mysql_atuais = {c.cd_cliente_oracle for c in clientes_oracle_mysql if c.cd_cliente_oracle}
            
            # Clientes para adicionar (estão no Oracle mas não no MySQL)
            codigos_para_adicionar = codigos_oracle_atuais - codigos_mysql_atuais
            print(f"📈 Clientes para adicionar: {len(codigos_para_adicionar)}")
            
            # Clientes para remover (estão no MySQL mas não no Oracle)
            codigos_para_remover = codigos_mysql_atuais - codigos_oracle_atuais
            print(f"📉 Clientes para remover: {len(codigos_para_remover)}")
            
            # Clientes para atualizar (continuam na lista)
            codigos_para_atualizar = codigos_oracle_atuais & codigos_mysql_atuais
            print(f"🔄 Clientes para atualizar: {len(codigos_para_atualizar)}")
            
            adicionados = 0
            removidos = 0
            atualizados = 0
            erros = []
            
            # Adicionar novos clientes
            if codigos_para_adicionar:
                print(f"\n➕ Adicionando {len(codigos_para_adicionar)} novos clientes...")
                for cliente_oracle in clientes_oracle:
                    cd_cliente = str(cliente_oracle.get('cd_cliente', ''))
                    if cd_cliente in codigos_para_adicionar:
                        try:
                            # Determinar consultor
                            consultor_oracle = cliente_oracle.get('consultor', '')
                            codigo_consultor = consultor_oracle.split(' - ')[0] if ' - ' in consultor_oracle else ''
                            consultor_id = mapa_consultores.get(codigo_consultor, consultores[0].id)
                            
                            # Criar novo cliente
                            novo_cliente = Cliente(
                                nome=cliente_oracle.get('cliente', '')[:200],
                                cnpj=cliente_oracle.get('cnpj'),
                                telefone=cliente_oracle.get('telefone'),
                                cd_cliente_oracle=cd_cliente,
                                categoria_consultor=cliente_oracle.get('consultor'),
                                conceito=cliente_oracle.get('conceito'),
                                representante_oracle=cliente_oracle.get('representante'),
                                valor_ultimo_pedido=cliente_oracle.get('total_pedido'),
                                situacao_ultimo_pedido=cliente_oracle.get('situacao'),
                                consultor_id=consultor_id,
                                origem='sincronizacao_oracle',
                                ativo=True
                            )
                            
                            dt_pedido = cliente_oracle.get('dt_pedido')
                            if dt_pedido:
                                novo_cliente.ultimo_pedido_oracle = dt_pedido
                            
                            novo_cliente.data_ultima_sincronizacao = datetime.now()
                            db.session.add(novo_cliente)
                            adicionados += 1
                            
                        except Exception as e:
                            erros.append(f"Erro ao adicionar {cd_cliente}: {str(e)}")
            
            # Remover clientes que sairam da lista
            if codigos_para_remover:
                print(f"\n➖ Removendo {len(codigos_para_remover)} clientes que sairam da lista...")
                for cliente_mysql in clientes_oracle_mysql:
                    if cliente_mysql.cd_cliente_oracle in codigos_para_remover:
                        try:
                            # Marcar como inativo em vez de deletar
                            cliente_mysql.ativo = False
                            cliente_mysql.data_ultima_sincronizacao = datetime.now()
                            removidos += 1
                        except Exception as e:
                            erros.append(f"Erro ao remover {cliente_mysql.cd_cliente_oracle}: {str(e)}")
            
            # Atualizar dados dos clientes existentes
            if codigos_para_atualizar:
                print(f"\n🔄 Atualizando {len(codigos_para_atualizar)} clientes existentes...")
                for cliente_oracle in clientes_oracle:
                    cd_cliente = str(cliente_oracle.get('cd_cliente', ''))
                    if cd_cliente in codigos_para_atualizar:
                        try:
                            cliente_mysql = Cliente.query.filter_by(cd_cliente_oracle=cd_cliente).first()
                            if cliente_mysql:
                                # Atualizar dados
                                cliente_mysql.categoria_consultor = cliente_oracle.get('consultor')
                                cliente_mysql.conceito = cliente_oracle.get('conceito')
                                cliente_mysql.representante_oracle = cliente_oracle.get('representante')
                                cliente_mysql.valor_ultimo_pedido = cliente_oracle.get('total_pedido')
                                cliente_mysql.situacao_ultimo_pedido = cliente_oracle.get('situacao')
                                
                                dt_pedido = cliente_oracle.get('dt_pedido')
                                if dt_pedido:
                                    cliente_mysql.ultimo_pedido_oracle = dt_pedido
                                
                                cliente_mysql.data_ultima_sincronizacao = datetime.now()
                                atualizados += 1
                        except Exception as e:
                            erros.append(f"Erro ao atualizar {cd_cliente}: {str(e)}")
            
            # Salvar todas as alterações
            print(f"\n💾 Salvando alterações no banco...")
            db.session.commit()
            
            # Estatísticas finais
            print("\n" + "=" * 60)
            print("📊 RESULTADO DA SINCRONIZAÇÃO AUTOMÁTICA")
            print("=" * 60)
            print(f"📈 Clientes adicionados: {adicionados}")
            print(f"📉 Clientes removidos: {removidos}")
            print(f"🔄 Clientes atualizados: {atualizados}")
            print(f"❌ Erros: {len(erros)}")
            print(f"📊 Total processado: {adicionados + removidos + atualizados}")
            
            # Verificar totais finais
            clientes_oracle_final = Cliente.query.filter(
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ativo == True
            ).count()
            
            print(f"\n📈 Estatísticas finais:")
            print(f"   Clientes Oracle ativos: {clientes_oracle_final}")
            print(f"   Clientes Oracle esperados: {len(clientes_oracle)}")
            print(f"   Diferença: {clientes_oracle_final - len(clientes_oracle)}")
            
            if erros:
                print(f"\n⚠️ Primeiros 5 erros:")
                for erro in erros[:5]:
                    print(f"   - {erro}")
            
            return True
            
    except Exception as e:
        print(f"\n❌ Erro na sincronização automática: {e}")
        return False

if __name__ == "__main__":
    try:
        resultado = sincronizacao_automatica_diaria()
        
        if resultado:
            print("\n🎉 SINCRONIZAÇÃO AUTOMÁTICA CONCLUÍDA!")
            print("\n📌 Este script será executado automaticamente todos os dias")
        else:
            print("\n❌ Falha na sincronização automática")
        
    except KeyboardInterrupt:
        print("\n\n⏹️ Sincronização interrompida")
    except Exception as e:
        print(f"\n❌ Erro geral: {e}")
    finally:
        print("\n🏁 Fim da sincronização automática")
        input("Pressione Enter para sair...")
