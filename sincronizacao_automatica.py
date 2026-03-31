"""
Scheduler para sincronização automática diária com Oracle
"""

import os
import sys
import logging
import unicodedata
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


def _normalizar_nome(txt: str) -> str:
    if not txt:
        return ""
    base = unicodedata.normalize("NFKD", str(txt))
    base = "".join(c for c in base if not unicodedata.combining(c))
    return " ".join(base.upper().strip().split())


def _extrair_nome_consultor_oracle(valor_oracle: str) -> str:
    if not valor_oracle:
        return ""
    partes = [p.strip() for p in str(valor_oracle).split("-") if p.strip()]
    if len(partes) >= 2:
        return partes[1]
    return partes[0] if partes else ""


def _resolver_consultor_id(
    consultor_oracle: str,
    mapa_codigo_para_id: dict,
    mapa_nome_para_id: dict,
    fallback_id: int,
) -> int:
    texto = str(consultor_oracle or "").strip()
    codigo = ""
    if " - " in texto:
        codigo = texto.split(" - ", 1)[0].strip()

    nome_oracle = _extrair_nome_consultor_oracle(texto)
    nome_norm = _normalizar_nome(nome_oracle)
    if nome_norm and nome_norm in mapa_nome_para_id:
        return mapa_nome_para_id[nome_norm]

    if codigo and codigo in mapa_codigo_para_id:
        return mapa_codigo_para_id[codigo]

    logger.warning(
        f"Consultor Oracle sem mapeamento seguro ({texto!r}); usando fallback_id={fallback_id}"
    )
    return fallback_id

def sincronizacao_automatica_diaria():
    """Sincronização automática diária com Oracle"""
    
    logger.info("=" * 60)
    logger.info("🔄 SINCRONIZAÇÃO AUTOMÁTICA DIÁRIA ORACLE")
    logger.info("=" * 60)
    logger.info(f"📅 Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    
    try:
        from app import app
        from core.extensions import db
        from core.models import Cliente, Ligacao, Usuario, SyncResumoDiario
        from oracle_service import (
            get_clientes_oracle,
            get_clientes_inativos_oracle,
            get_clientes_proximos_inativacao_oracle,
            get_valor_total_365dias,
        )
        
        with app.app_context():
            data_sync = datetime.now()
            data_ref = data_sync.date()
            limite_max = data_sync - timedelta(days=181)
            limite_min = data_sync - timedelta(days=730)

            inativos_antes_sync = {
                str(c.cd_cliente_oracle).strip()
                for c in Cliente.query.filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_min, limite_max),
                ).all()
                if c.cd_cliente_oracle
            }

            logger.info("\n🔍 1. Buscando clientes Oracle (90-120d, proximos 151-180d e inativos 181-730d)...")
            clientes_oracle_90_120 = get_clientes_oracle()
            clientes_oracle_proximos = get_clientes_proximos_inativacao_oracle()
            clientes_oracle_inativos = get_clientes_inativos_oracle()

            clientes_oracle_por_cd = {}
            for row in (clientes_oracle_90_120 + clientes_oracle_proximos + clientes_oracle_inativos):
                cd_cliente = str(row.get('cd_cliente', '')).strip()
                if not cd_cliente:
                    continue
                atual = clientes_oracle_por_cd.get(cd_cliente)
                if not atual:
                    clientes_oracle_por_cd[cd_cliente] = row
                    continue
                dt_novo = row.get('dt_pedido')
                dt_atual = atual.get('dt_pedido')
                if dt_novo and (not dt_atual or dt_novo > dt_atual):
                    clientes_oracle_por_cd[cd_cliente] = row

            clientes_oracle = list(clientes_oracle_por_cd.values())
            codigos_alvo_90_120 = {
                str(c.get('cd_cliente', '')).strip()
                for c in clientes_oracle_90_120
                if c.get('cd_cliente')
            }
            logger.info(f"✅ {len(clientes_oracle_90_120)} clientes 90-120d encontrados no Oracle")
            logger.info(f"✅ {len(clientes_oracle_proximos)} clientes proximos de inativacao (151-180d) encontrados no Oracle")
            logger.info(f"✅ {len(clientes_oracle_inativos)} clientes inativos (181-730d) encontrados no Oracle")
            logger.info(f"✅ {len(clientes_oracle)} clientes únicos para sincronizar")
            
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
            consultores = Usuario.query.filter(
                Usuario.tipo.in_(['consultor', 'televendas']), 
                Usuario.ativo == True
            ).all()
            logger.info(f" {len(consultores)} consultores/televendas ativos encontrados")
            
            usuarios_mapa = Usuario.query.filter(
                Usuario.tipo.in_(['consultor', 'televendas', 'supervisor']),
                Usuario.ativo == True
            ).all()
            consultores_por_nome = {
                _normalizar_nome(u.nome): u.id
                for u in usuarios_mapa
            }

            mapa_codigo_nome = {
                '100': 'Roseleia Basso',
                '002': 'Rodrigo Crespan',
                '007': 'Janine de Mello',
                '012': 'Sandra Vendruscolo da Silva',
                '001': 'Elisabete Haus',
                '003': 'Iara Sponchiado',
                '004': 'Odete Luza',
                '005': 'Carla Siduoski',
                '006': 'Sibele Froner',
                '010': 'Sibele Froner',
                '999': 'Daniela Da Rosa',
            }
            mapa_codigo_para_id = {}
            for codigo, nome_ref in mapa_codigo_nome.items():
                usr_id = consultores_por_nome.get(_normalizar_nome(nome_ref))
                if usr_id:
                    mapa_codigo_para_id[codigo] = usr_id
                else:
                    logger.warning(
                        f"Codigo Oracle {codigo} sem usuario ativo correspondente para nome de referencia {nome_ref!r}"
                    )
            
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
                            with db.session.begin_nested():
                                # Determinar consultor
                                consultor_oracle = cliente_oracle.get('consultor', '')
                                consultor_id = _resolver_consultor_id(
                                    consultor_oracle=consultor_oracle,
                                    mapa_codigo_para_id=mapa_codigo_para_id,
                                    mapa_nome_para_id=consultores_por_nome,
                                    fallback_id=consultores[0].id,
                                )

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
                                    cnpj=(str(cliente_oracle.get('cnpj')).strip() if cliente_oracle.get('cnpj') is not None else None),
                                    telefone=telefone_principal,
                                    telefone2=telefone2_padronizado,
                                    cd_cliente_oracle=cd_cliente,
                                    categoria_consultor=cliente_oracle.get('consultor'),
                                    conceito=cliente_oracle.get('conceito'),
                                    representante_oracle=cliente_oracle.get('representante'),
                                    municipio=cliente_oracle.get('municipio'),
                                    uf=cliente_oracle.get('uf'),
                                    contato=cliente_oracle.get('contato'),
                                    valor_ultimo_pedido=cliente_oracle.get('total_pedido'),
                                    situacao_ultimo_pedido=cliente_oracle.get('situacao'),
                                    consultor_id=consultor_id,
                                    origem='importado_csv',
                                    ativo=True
                                )

                                dt_pedido = cliente_oracle.get('dt_pedido')
                                if dt_pedido:
                                    novo_cliente.ultimo_pedido_oracle = dt_pedido

                                # Evita custo alto em massa para inativos; mantém cálculo completo para 90-120d.
                                if cd_cliente in codigos_alvo_90_120:
                                    try:
                                        valor_total_365 = get_valor_total_365dias(cd_cliente)
                                        novo_cliente.valor_total_365dias = valor_total_365
                                        logger.info(f"   Valor 365 dias para {cd_cliente}: R$ {valor_total_365:.2f}")
                                    except Exception as e:
                                        logger.warning(f"   Erro ao calcular valor 365 dias para {cd_cliente}: {str(e)}")
                                        novo_cliente.valor_total_365dias = 0.0
                                else:
                                    novo_cliente.valor_total_365dias = 0.0

                                novo_cliente.data_ultima_sincronizacao = data_sync
                                db.session.add(novo_cliente)
                                db.session.flush()
                            adicionados += 1
                            
                        except ValueError as e:
                            erros.append(f"Erro de dados ao adicionar {cd_cliente}: {str(e)}")
                        except IntegrityError as e:
                            erros.append(f"Erro de integridade ao adicionar {cd_cliente}: {str(e)}")
                        except Exception as e:
                            erros.append(f"Erro ao adicionar {cd_cliente}: {str(e)}")
            
            # Remover clientes que sairam da lista
            if codigos_para_remover:
                logger.info(f"\n➖ Removendo {len(codigos_para_remover)} clientes que sairam da lista...")
                for cliente_mysql in clientes_oracle_mysql:
                    if cliente_mysql.cd_cliente_oracle in codigos_para_remover:
                        try:
                            with db.session.begin_nested():
                                # Marcar como inativo em vez de deletar
                                cliente_mysql.ativo = False
                                cliente_mysql.data_ultima_sincronizacao = data_sync
                                db.session.flush()
                            removidos += 1
                        except ValueError as e:
                            erros.append(f"Erro de dados ao remover {cliente_mysql.cd_cliente_oracle}: {str(e)}")
                        except Exception as e:
                            erros.append(f"Erro ao remover {cliente_mysql.cd_cliente_oracle}: {str(e)}")
            
            # Atualizar dados dos clientes existentes
            if codigos_para_atualizar:
                logger.info(f"\n🔄 Atualizando {len(codigos_para_atualizar)} clientes existentes...")
                for cliente_oracle in clientes_oracle:
                    cd_cliente = str(cliente_oracle.get('cd_cliente', ''))
                    if cd_cliente in codigos_para_atualizar:
                        try:
                            with db.session.begin_nested():
                                cliente_mysql = Cliente.query.filter_by(cd_cliente_oracle=cd_cliente).first()
                                if cliente_mysql:
                                    # Determinar consultor atualizado
                                    consultor_oracle = cliente_oracle.get('consultor', '')
                                    consultor_id = _resolver_consultor_id(
                                        consultor_oracle=consultor_oracle,
                                        mapa_codigo_para_id=mapa_codigo_para_id,
                                        mapa_nome_para_id=consultores_por_nome,
                                        fallback_id=cliente_mysql.consultor_id,
                                    )

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

                                    cnpj_oracle = cliente_oracle.get('cnpj')
                                    if cnpj_oracle is not None and str(cnpj_oracle).strip():
                                        cliente_mysql.cnpj = str(cnpj_oracle).strip()

                                    # Não sobrescreve com vazio para evitar "sumiço" de dados locais.
                                    if telefone_principal:
                                        cliente_mysql.telefone = telefone_principal
                                    if telefone2_padronizado:
                                        cliente_mysql.telefone2 = telefone2_padronizado

                                    cliente_mysql.categoria_consultor = cliente_oracle.get('consultor')
                                    cliente_mysql.conceito = cliente_oracle.get('conceito')
                                    cliente_mysql.representante_oracle = cliente_oracle.get('representante')
                                    cliente_mysql.municipio = cliente_oracle.get('municipio')
                                    cliente_mysql.uf = cliente_oracle.get('uf')
                                    cliente_mysql.contato = cliente_oracle.get('contato')
                                    cliente_mysql.valor_ultimo_pedido = cliente_oracle.get('total_pedido')
                                    cliente_mysql.situacao_ultimo_pedido = cliente_oracle.get('situacao')

                                    # Preserva carteira operacional de televendas quando já houve
                                    # contato/retorno no app para evitar "sumiço" de listas.
                                    preservar_carteira_manual = (
                                        str(getattr(cliente_mysql, 'origem', '') or '').strip().lower() == 'manual'
                                    )
                                    manter_carteira_televendas = False
                                    dono_atual = db.session.get(Usuario, cliente_mysql.consultor_id) if cliente_mysql.consultor_id else None
                                    if dono_atual and dono_atual.tipo == 'televendas':
                                        teve_ligacao_tv = (
                                            db.session.query(Ligacao.id)
                                            .filter(
                                                Ligacao.cliente_id == cliente_mysql.id,
                                                Ligacao.consultor_id == cliente_mysql.consultor_id
                                            )
                                            .first()
                                            is not None
                                        )
                                        manter_carteira_televendas = bool(
                                            cliente_mysql.proxima_ligacao is not None or teve_ligacao_tv
                                        )

                                    if not preservar_carteira_manual and not manter_carteira_televendas:
                                        cliente_mysql.consultor_id = consultor_id  # ATUALIZAR CONSULTOR

                                    dt_pedido = cliente_oracle.get('dt_pedido')
                                    if dt_pedido:
                                        cliente_mysql.ultimo_pedido_oracle = dt_pedido

                                    # Evita custo alto em massa para inativos; mantém cálculo completo para 90-120d.
                                    if cd_cliente in codigos_alvo_90_120:
                                        try:
                                            valor_total_365 = get_valor_total_365dias(cd_cliente)
                                            cliente_mysql.valor_total_365dias = valor_total_365
                                            logger.info(f"   Valor 365 dias para {cd_cliente}: R$ {valor_total_365:.2f}")
                                        except Exception as e:
                                            logger.warning(f"   Erro ao calcular valor 365 dias para {cd_cliente}: {str(e)}")
                                            cliente_mysql.valor_total_365dias = 0.0

                                    cliente_mysql.data_ultima_sincronizacao = data_sync
                                    db.session.flush()
                                    atualizados += 1
                        except ValueError as e:
                            erros.append(f"Erro de dados ao atualizar {cd_cliente}: {str(e)}")
                        except Exception as e:
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

            inativos_depois_sync = {
                str(c.cd_cliente_oracle).strip()
                for c in Cliente.query.filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_min, limite_max),
                ).all()
                if c.cd_cliente_oracle
            }
            inativos_entraram = len(inativos_depois_sync - inativos_antes_sync)
            inativos_sairam = len(inativos_antes_sync - inativos_depois_sync)

            resumo = SyncResumoDiario.query.filter_by(data_ref=data_ref).first()
            if not resumo:
                resumo = SyncResumoDiario(data_ref=data_ref)
                db.session.add(resumo)
            resumo.inativos_entraram = inativos_entraram
            resumo.inativos_sairam = inativos_sairam
            resumo.total_inativos = len(inativos_depois_sync)
            resumo.atualizado_em = data_sync
            db.session.commit()
            logger.info(
                "📉 Movimento inativos hoje: +%s / -%s (total %s)",
                inativos_entraram,
                inativos_sairam,
                len(inativos_depois_sync),
            )
            
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
