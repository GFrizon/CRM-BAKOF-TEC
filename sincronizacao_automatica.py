"""
Scheduler para sincronização automática diária com Oracle
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError

# Importar utilitários de telefone
from telefone_utils import padronizar_telefone, identificar_ddd_padrao

# Carregar .env
load_dotenv('.env')

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Adicionar diretório atual ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def sincronizacao_automatica_diaria():
    """Sincronização automática diária com Oracle"""
    
    logger.info("=" * 60)
    logger.info("🔄 SINCRONIZAÇÃO AUTOMÁTICA DIÁRIA ORACLE")
    logger.info("=" * 60)
    logger.info(f"📅 Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    
    try:
        from app import app, db, Cliente, Usuario
        from oracle_service import get_clientes_oracle, get_valor_total_365dias
        
        with app.app_context():
            logger.info("\n🔍 1. Buscando clientes alvo no Oracle...")
            clientes_oracle = get_clientes_oracle()
            logger.info(f"✅ {len(clientes_oracle)} clientes alvo encontrados no Oracle")
            
            logger.info("\n📊 2. Analisando clientes atuais no MySQL...")
            todos_clientes_mysql = Cliente.query.filter_by(ativo=True).all()
            logger.info(f"✅ {len(todos_clientes_mysql)} clientes totais no MySQL")
            
            # Separar clientes por origem
            clientes_oracle_mysql = Cliente.query.filter(
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ativo == True
            ).all()
            logger.info(f" {len(clientes_oracle_mysql)} clientes vindos do Oracle")
            
            logger.info("\n Verificando consultores disponíveis...")
            consultores = Usuario.query.filter_by(tipo='consultor', ativo=True).all()
            logger.info(f" {len(consultores)} consultores ativos encontrados")
            
            # Criar mapa de códigos Oracle para consultores
            mapa_consultores = {
                '100': 24,  # ROSELEIA → Roseleia Basso
                '002': 23,  # RODRIGO → Rodrigo Crespan
                '012': 25,  # SANDRA → Sandra Vendruscolo da Silva
                '001': 19,  # Elisabete Haus
                '003': 20,  # Janine de Mello
                '004': 21,  # Iara Sponchiado (exceto ODETE)
                '005': 22,  # Carla Siduoski
                '006': 26,  # Sibele Froner
                '010': 26,  # SIBELE → Sibele Froner
            }
            
            logger.info("\n Processando sincronização...")
            
            # Detectar DDD mais comum para usar como padrão
            todos_telefones = []
            for cliente_oracle in clientes_oracle:
                todos_telefones.extend([
                    cliente_oracle.get('telefone1'),
                    cliente_oracle.get('telefone2')
                ])
            
            ddd_padrao = identificar_ddd_padrao(todos_telefones)
            if ddd_padrao:
                logger.info(f" DDD padrão detectado: {ddd_padrao}")
            else:
                logger.info(" Nenhum DDD padrão detectado")
            
            # Conjuntos para controle
            codigos_oracle_atuais = {str(c.get('cd_cliente', '')) for c in clientes_oracle}
            codigos_mysql_atuais = {c.cd_cliente_oracle for c in clientes_oracle_mysql if c.cd_cliente_oracle}
            
            # Clientes para adicionar (estão no Oracle mas não no MySQL)
            codigos_para_adicionar = codigos_oracle_atuais - codigos_mysql_atuais
            logger.info(f" Análise de sincronização:")
            logger.info(f"   Novos clientes: {len(codigos_para_adicionar)}")
            
            # Clientes para remover (estão no MySQL mas não no Oracle)
            codigos_para_remover = codigos_mysql_atuais - codigos_oracle_atuais
            logger.info(f"   Clientes para remover: {len(codigos_para_remover)}")
            
            # Clientes para atualizar (continuam na lista)
            codigos_para_atualizar = codigos_oracle_atuais & codigos_mysql_atuais
            logger.info(f"   Clientes para atualizar: {len(codigos_para_atualizar)}")
            
            adicionados = 0
            removidos = 0
            atualizados = 0
            erros = []
            
            # Adicionar novos clientes
            if codigos_para_adicionar:
                logger.info(f"\n➕ Adicionando {len(codigos_para_adicionar)} novos clientes...")
                for cliente_oracle in clientes_oracle:
                    cd_cliente = str(cliente_oracle.get('cd_cliente', ''))
                    if cd_cliente in codigos_para_adicionar:
                        try:
                            # Determinar consultor
                            consultor_oracle = cliente_oracle.get('consultor', '')
                            codigo_consultor = consultor_oracle.split(' - ')[0] if ' - ' in consultor_oracle else ''
                            
                            # Regras especiais
                            if 'ODETE' in consultor_oracle:
                                consultor_id = 27  # Odete Luza
                            elif 'IARA' in consultor_oracle:
                                consultor_id = 21  # Iara Sponchiado
                            elif 'JANINE' in consultor_oracle:
                                consultor_id = 20  # Janine de Mello
                            else:
                                consultor_id = mapa_consultores.get(codigo_consultor, consultores[0].id)
                            
                            # Criar novo cliente
                            # Tratar telefones: se telefone1 estiver vazio, usar telefone2
                            telefone1_bruto = cliente_oracle.get('telefone1')
                            telefone2_bruto = cliente_oracle.get('telefone2')
                            
                            # Padronizar telefones
                            telefone1_padronizado = padronizar_telefone(telefone1_bruto, ddd_padrao)
                            telefone2_padronizado = padronizar_telefone(telefone2_bruto, ddd_padrao)
                            
                            # Definir telefone principal (padronizado)
                            telefone_principal = telefone1_padronizado
                            if not telefone_principal:
                                telefone_principal = telefone2_padronizado
                            
                            novo_cliente = Cliente(
                                nome=cliente_oracle.get('cliente', '')[:200],
                                cnpj=cliente_oracle.get('cnpj'),
                                telefone=telefone_principal,
                                telefone2=telefone2_padronizado,
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
                            
                            # Calcular valor total dos últimos 365 dias
                            try:
                                valor_total_365 = get_valor_total_365dias(cd_cliente)
                                novo_cliente.valor_total_365dias = valor_total_365
                                logger.info(f"   Valor 365 dias para {cd_cliente}: R$ {valor_total_365:.2f}")
                            except Exception as e:
                                logger.warning(f"   Erro ao calcular valor 365 dias para {cd_cliente}: {str(e)}")
                                novo_cliente.valor_total_365dias = 0.0
                            
                            novo_cliente.data_ultima_sincronizacao = datetime.now()
                            db.session.add(novo_cliente)
                            adicionados += 1
                            
                        except ValueError as e:
                            erros.append(f"Erro de dados ao adicionar {cd_cliente}: {str(e)}")
                        except IntegrityError as e:
                            db.session.rollback()
                            erros.append(f"Erro de integridade ao adicionar {cd_cliente}: {str(e)}")
                        except Exception as e:
                            db.session.rollback()
                            erros.append(f"Erro ao adicionar {cd_cliente}: {str(e)}")
            
            # Remover clientes que sairam da lista
            if codigos_para_remover:
                logger.info(f"\n➖ Removendo {len(codigos_para_remover)} clientes que sairam da lista...")
                for cliente_mysql in clientes_oracle_mysql:
                    if cliente_mysql.cd_cliente_oracle in codigos_para_remover:
                        try:
                            # Marcar como inativo em vez de deletar
                            cliente_mysql.ativo = False
                            cliente_mysql.data_ultima_sincronizacao = datetime.now()
                            removidos += 1
                        except ValueError as e:
                            erros.append(f"Erro de dados ao remover {cliente_mysql.cd_cliente_oracle}: {str(e)}")
                        except Exception as e:
                            db.session.rollback()
                            erros.append(f"Erro ao remover {cliente_mysql.cd_cliente_oracle}: {str(e)}")
            
            # Atualizar dados dos clientes existentes
            if codigos_para_atualizar:
                logger.info(f"\n🔄 Atualizando {len(codigos_para_atualizar)} clientes existentes...")
                for cliente_oracle in clientes_oracle:
                    cd_cliente = str(cliente_oracle.get('cd_cliente', ''))
                    if cd_cliente in codigos_para_atualizar:
                        try:
                            cliente_mysql = Cliente.query.filter_by(cd_cliente_oracle=cd_cliente).first()
                            if cliente_mysql:
                                # Determinar consultor atualizado
                                consultor_oracle = cliente_oracle.get('consultor', '')
                                codigo_consultor = consultor_oracle.split(' - ')[0] if ' - ' in consultor_oracle else ''
                                
                                # Regras especiais
                                if 'ODETE' in consultor_oracle:
                                    consultor_id = 27  # Odete Luza
                                elif 'IARA' in consultor_oracle:
                                    consultor_id = 21  # Iara Sponchiado
                                elif 'JANINE' in consultor_oracle:
                                    consultor_id = 20  # Janine de Mello
                                else:
                                    consultor_id = mapa_consultores.get(codigo_consultor, cliente_mysql.consultor_id)
                                
                                # Atualizar dados
                                # Tratar telefones: se telefone1 estiver vazio, usar telefone2
                                telefone1_bruto = cliente_oracle.get('telefone1')
                                telefone2_bruto = cliente_oracle.get('telefone2')
                                
                                # Padronizar telefones
                                telefone1_padronizado = padronizar_telefone(telefone1_bruto, ddd_padrao)
                                telefone2_padronizado = padronizar_telefone(telefone2_bruto, ddd_padrao)
                                
                                # Definir telefone principal (padronizado)
                                telefone_principal = telefone1_padronizado
                                if not telefone_principal:
                                    telefone_principal = telefone2_padronizado
                                
                                cliente_mysql.telefone = telefone_principal
                                cliente_mysql.telefone2 = telefone2_padronizado
                                cliente_mysql.categoria_consultor = cliente_oracle.get('consultor')
                                cliente_mysql.conceito = cliente_oracle.get('conceito')
                                cliente_mysql.representante_oracle = cliente_oracle.get('representante')
                                cliente_mysql.valor_ultimo_pedido = cliente_oracle.get('total_pedido')
                                cliente_mysql.situacao_ultimo_pedido = cliente_oracle.get('situacao')
                                cliente_mysql.consultor_id = consultor_id  # ATUALIZAR CONSULTOR
                                
                                dt_pedido = cliente_oracle.get('dt_pedido')
                                if dt_pedido:
                                    cliente_mysql.ultimo_pedido_oracle = dt_pedido
                                
                                # Calcular valor total dos últimos 365 dias
                                try:
                                    valor_total_365 = get_valor_total_365dias(cd_cliente)
                                    cliente_mysql.valor_total_365dias = valor_total_365
                                    logger.info(f"   Valor 365 dias para {cd_cliente}: R$ {valor_total_365:.2f}")
                                except Exception as e:
                                    logger.warning(f"   Erro ao calcular valor 365 dias para {cd_cliente}: {str(e)}")
                                    cliente_mysql.valor_total_365dias = 0.0
                                
                                cliente_mysql.data_ultima_sincronizacao = datetime.now()
                                atualizados += 1
                        except ValueError as e:
                            erros.append(f"Erro de dados ao atualizar {cd_cliente}: {str(e)}")
                        except Exception as e:
                            db.session.rollback()
                            erros.append(f"Erro ao atualizar {cd_cliente}: {str(e)}")
            
            # Salvar todas as alterações
            logger.info(f"\n💾 Salvando alterações no banco...")
            try:
                db.session.commit()
            except IntegrityError as e:
                db.session.rollback()
                logger.error(f"❌ Erro de integridade ao salvar: {str(e)}")
                return False
            except Exception as e:
                db.session.rollback()
                logger.error(f"❌ Erro ao salvar alterações: {str(e)}")
                return False
            
            # Estatísticas finais
            logger.info("\n" + "=" * 60)
            logger.info("📊 RESULTADO DA SINCRONIZAÇÃO AUTOMÁTICA")
            logger.info("=" * 60)
            logger.info(f"📈 Clientes adicionados: {adicionados}")
            logger.info(f"📉 Clientes removidos: {removidos}")
            logger.info(f"🔄 Clientes atualizados: {atualizados}")
            logger.info(f"❌ Erros: {len(erros)}")
            logger.info(f"📊 Total processado: {adicionados + removidos + atualizados}")
            
            # Verificar totais finais
            clientes_oracle_final = Cliente.query.filter(
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ativo == True
            ).count()
            
            logger.info(f"\n📈 Estatísticas finais:")
            logger.info(f"   Clientes Oracle ativos: {clientes_oracle_final}")
            logger.info(f"   Clientes Oracle esperados: {len(clientes_oracle)}")
            logger.info(f"   Diferença: {clientes_oracle_final - len(clientes_oracle)}")
            
            if erros:
                logger.warning(f"\n⚠️ Primeiros 5 erros:")
                for erro in erros[:5]:
                    logger.warning(f"   - {erro}")
            
            return True
            
    except Exception as e:
        logger.error(f"\n❌ Erro na sincronização automática: {e}")
        return False

if __name__ == "__main__":
    try:
        resultado = sincronizacao_automatica_diaria()
        
        if resultado:
            logger.info("\n🎉 SINCRONIZAÇÃO AUTOMÁTICA CONCLUÍDA!")
            logger.info("\n📌 Este script será executado automaticamente todos os dias")
        else:
            logger.error("\n❌ Falha na sincronização automática")
        
    except KeyboardInterrupt:
        logger.warning("\n\n⏹️ Sincronização interrompida")
    except Exception as e:
        logger.error(f"\n❌ Erro geral: {e}")
    finally:
        logger.info("\n🏁 Fim da sincronização automática")
        input("Pressione Enter para sair...")
