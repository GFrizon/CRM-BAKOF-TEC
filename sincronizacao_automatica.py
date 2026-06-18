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
        codigo = texto.split(" - ", 1)[0].strip().lstrip("'\"")  # Remove aspas do código

    nome_oracle = _extrair_nome_consultor_oracle(texto)
    nome_norm = _normalizar_nome(nome_oracle)
    
    # Tentativa 1: match exato por nome
    if nome_norm and nome_norm in mapa_nome_para_id:
        return mapa_nome_para_id[nome_norm]
    
    # Tentativa 2: match por substring (nome Oracle contido no nome local)
    if nome_norm:
        for nome_local, uid in mapa_nome_para_id.items():
            if nome_norm in nome_local or nome_local in nome_norm:
                return uid
    
    # Tentativa 3: match exato por código
    if codigo and codigo in mapa_codigo_para_id:
        return mapa_codigo_para_id[codigo]
    
    # Tentativa 4: buscar por código contido no texto (fallback)
    if codigo:
        for codigo_mapa, uid in mapa_codigo_para_id.items():
            if codigo_mapa in texto or codigo in codigo_mapa:
                return uid

    logger.warning(
        f"Consultor Oracle sem mapeamento seguro ({texto!r}); usando fallback_id={fallback_id}"
    )
    return fallback_id

def sincronizacao_automatica_diaria(app):
    """Sincronização automática diária com Oracle"""
    
    logger.info("=" * 60)
    logger.info("🔄 SINCRONIZAÇÃO AUTOMÁTICA DIÁRIA ORACLE")
    logger.info("=" * 60)
    logger.info(f"📅 Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    
    try:
        from core.extensions import db
        from core.models import Cliente, Ligacao, Usuario, SyncResumoDiario
        from oracle_service import (
            get_clientes_oracle,
            get_clientes_ativos_oracle,
            get_clientes_construtoras_oracle,
            get_clientes_inativos_oracle,
            get_clientes_proximos_inativacao_oracle,
            get_valor_total_365dias,
        )
        from services.inativos_movimento_service import salvar_movimento_inativos
        from services.ativos_snapshot_service import salvar_snapshot_ativos_oracle
        from services.construtoras_snapshot_service import salvar_snapshot_construtoras_oracle
        from services.inativos_snapshot_service import salvar_snapshot_inativos_oracle
        from services.oracle_snapshot_service import salvar_snapshot_oracle_90_150
        from services.proximos_inativacao_snapshot_service import (
            salvar_snapshot_proximos_inativacao,
        )
        from services.carteiras_movimento_service import (
            carregar_movimento_carteira_anterior,
            salvar_movimento_carteira,
        )
        
        with app.app_context():
            data_sync = datetime.now()
            data_ref = data_sync.date()
            limite_90 = data_sync - timedelta(days=90)
            limite_150 = data_sync - timedelta(days=150)
            limite_151 = data_sync - timedelta(days=151)
            limite_180 = data_sync - timedelta(days=180)
            limite_max = data_sync - timedelta(days=181)
            limite_min = data_sync - timedelta(days=1095)

            faixa_90_150_antes_sync = {
                str(c.cd_cliente_oracle).strip()
                for c in Cliente.query.filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_150, limite_90),
                ).all()
                if c.cd_cliente_oracle
            }
            faixa_proximos_antes_sync = {
                str(c.cd_cliente_oracle).strip()
                for c in Cliente.query.filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_180, limite_151),
                ).all()
                if c.cd_cliente_oracle
            }
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

            logger.info("\n🔍 1. Buscando carteiras Oracle e renovando snapshots diarios...")
            clientes_oracle_ativos = get_clientes_ativos_oracle()
            clientes_oracle_90_150 = get_clientes_oracle()
            clientes_oracle_proximos = get_clientes_proximos_inativacao_oracle()
            clientes_oracle_inativos = get_clientes_inativos_oracle()
            salvar_snapshot_ativos_oracle(clientes_oracle_ativos, data_ref)
            salvar_snapshot_inativos_oracle(clientes_oracle_inativos, data_ref)
            salvar_snapshot_oracle_90_150(clientes_oracle_90_150, data_ref)
            salvar_snapshot_proximos_inativacao(clientes_oracle_proximos, data_ref)

            # A carteira de construtoras e independente da sincronizacao local.
            # Uma indisponibilidade nessa consulta nao deve abortar as carteiras principais.
            clientes_oracle_construtoras = None
            try:
                clientes_oracle_construtoras = get_clientes_construtoras_oracle()
                salvar_snapshot_construtoras_oracle(clientes_oracle_construtoras, data_ref)
            except Exception as construtoras_error:
                logger.warning(
                    "Falha ao renovar snapshot de construtoras; mantendo o ultimo valido: %s",
                    construtoras_error,
                )

            clientes_oracle_por_cd = {}
            # NOTA: clientes_oracle_ativos (0-180d) NAO entra na lista de sync local.
            # Eles servem apenas para o snapshot da aba "Clientes Ativos".
            # Incluir na sync criaria registros locais duplicados e inflaria "Clientes Especiais".
            for row in (clientes_oracle_90_150 + clientes_oracle_proximos + clientes_oracle_inativos):
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
            codigos_alvo_90_150 = {
                str(c.get('cd_cliente', '')).strip()
                for c in clientes_oracle_90_150
                if c.get('cd_cliente')
            }
            logger.info(f"✅ {len(clientes_oracle_ativos)} clientes ativos (0-180d) encontrados no Oracle")
            logger.info(f"✅ {len(clientes_oracle_90_150)} clientes 90-150d encontrados no Oracle")
            logger.info(f"✅ {len(clientes_oracle_proximos)} clientes proximos de inativacao (151-180d) encontrados no Oracle")
            logger.info(f"✅ {len(clientes_oracle_inativos)} clientes inativos (181-1095d) encontrados no Oracle")
            if clientes_oracle_construtoras is not None:
                logger.info(f"✅ {len(clientes_oracle_construtoras)} construtoras encontradas no Oracle")
            logger.info(f"✅ {len(clientes_oracle)} clientes únicos para sincronizar")
            
            logger.info("\n📊 2. Analisando clientes atuais no MySQL...")
            todos_clientes_mysql = Cliente.query.filter_by(ativo=True).all()
            logger.info(f"✅ {len(todos_clientes_mysql)} clientes totais no MySQL")
            
            # Separar clientes Oracle locais (ativos e inativos)
            clientes_oracle_mysql_todos = Cliente.query.filter(
                Cliente.cd_cliente_oracle.isnot(None),
            ).all()
            clientes_oracle_mysql_ativos = [c for c in clientes_oracle_mysql_todos if c.ativo]
            logger.info(
                f" {len(clientes_oracle_mysql_todos)} clientes Oracle locais "
                f"({len(clientes_oracle_mysql_ativos)} ativos)"
            )
            
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
            codigos_mysql_atuais = {c.cd_cliente_oracle for c in clientes_oracle_mysql_todos if c.cd_cliente_oracle}
            
            # Clientes para adicionar (estão no Oracle mas não no MySQL)
            codigos_para_adicionar = codigos_oracle_atuais - codigos_mysql_atuais
            logger.info(f" Análise de sincronização:")
            logger.info(f"   Novos clientes: {len(codigos_para_adicionar)}")
            
            # Clientes para remover (estão no MySQL mas não no Oracle)
            codigos_ativos_mysql = {c.cd_cliente_oracle for c in clientes_oracle_mysql_ativos if c.cd_cliente_oracle}
            codigos_para_remover = codigos_ativos_mysql - codigos_oracle_atuais
            logger.info(f"   Clientes para remover: {len(codigos_para_remover)}")
            
            # Clientes para atualizar (continuam na lista)
            codigos_para_atualizar = codigos_oracle_atuais & codigos_mysql_atuais
            logger.info(f"   Clientes para atualizar: {len(codigos_para_atualizar)}")
            
            adicionados = 0
            removidos = 0
            atualizados = 0
            reativados = 0
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
                                    origem='oracle_sync',
                                    ativo=True
                                )

                                dt_pedido = cliente_oracle.get('dt_pedido')
                                if dt_pedido:
                                    novo_cliente.ultimo_pedido_oracle = dt_pedido

                                # Evita custo alto em massa para inativos; mantém cálculo completo para 90-150d.
                                if cd_cliente in codigos_alvo_90_150:
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
                for cliente_mysql in clientes_oracle_mysql_ativos:
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
                                cliente_mysql = (
                                    Cliente.query
                                    .filter_by(cd_cliente_oracle=cd_cliente)
                                    .order_by(Cliente.ativo.desc(), Cliente.id.desc())
                                    .first()
                                )
                                if cliente_mysql:
                                    estava_inativo = not bool(cliente_mysql.ativo)
                                    # Se voltou na lista Oracle (90-150 / proximos / inativos), precisa aparecer.
                                    cliente_mysql.ativo = True
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

                                    # Evita custo alto em massa para inativos; mantém cálculo completo para 90-150d.
                                    if cd_cliente in codigos_alvo_90_150:
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
                                    if estava_inativo:
                                        reativados += 1
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
            logger.info(f"♻️ Clientes reativados: {reativados}")
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

            faixa_90_150_depois_sync = {
                str(c.cd_cliente_oracle).strip()
                for c in Cliente.query.filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_150, limite_90),
                ).all()
                if c.cd_cliente_oracle
            }
            faixa_proximos_depois_sync = {
                str(c.cd_cliente_oracle).strip()
                for c in Cliente.query.filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_180, limite_151),
                ).all()
                if c.cd_cliente_oracle
            }
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
            mov_90_150_anterior = carregar_movimento_carteira_anterior("oracle_90_150", data_ref) or {}
            mov_proximos_anterior = carregar_movimento_carteira_anterior("proximos_inativacao", data_ref) or {}

            codigos_90_150_base = {
                str(cd or "").strip()
                for cd in list((mov_90_150_anterior or {}).get("codigos_presentes") or [])
                if str(cd or "").strip()
            }
            if not codigos_90_150_base:
                codigos_90_150_base = set(faixa_90_150_antes_sync)

            codigos_proximos_base = {
                str(cd or "").strip()
                for cd in list((mov_proximos_anterior or {}).get("codigos_presentes") or [])
                if str(cd or "").strip()
            }
            if not codigos_proximos_base:
                codigos_proximos_base = set(faixa_proximos_antes_sync)

            codigos_90_150_entraram = sorted(faixa_90_150_depois_sync - codigos_90_150_base)
            codigos_90_150_sairam = sorted(codigos_90_150_base - faixa_90_150_depois_sync)
            codigos_proximos_entraram = sorted(faixa_proximos_depois_sync - codigos_proximos_base)
            codigos_proximos_sairam = sorted(codigos_proximos_base - faixa_proximos_depois_sync)
            codigos_entraram = sorted(inativos_depois_sync - inativos_antes_sync)
            codigos_sairam = sorted(inativos_antes_sync - inativos_depois_sync)
            inativos_entraram = len(codigos_entraram)
            inativos_sairam = len(codigos_sairam)

            resumo = SyncResumoDiario.query.filter_by(data_ref=data_ref).first()
            if not resumo:
                resumo = SyncResumoDiario(data_ref=data_ref)
                db.session.add(resumo)
            resumo.inativos_entraram = inativos_entraram
            resumo.inativos_sairam = inativos_sairam
            resumo.total_inativos = len(inativos_depois_sync)
            resumo.atualizado_em = data_sync
            db.session.commit()

            # Persistir detalhes diarios (clientes que entraram/sairam das carteiras)
            codigos_movimento = (
                set(codigos_entraram)
                | set(codigos_sairam)
                | set(codigos_90_150_entraram)
                | set(codigos_90_150_sairam)
                | set(codigos_proximos_entraram)
                | set(codigos_proximos_sairam)
            )
            detalhes_por_codigo = {}
            mapa_cliente_por_codigo = {}
            if codigos_movimento:
                rows_mov = (
                    Cliente.query
                    .filter(Cliente.cd_cliente_oracle.in_(list(codigos_movimento)))
                    .all()
                )

                cliente_ids_mov = [c.id for c in rows_mov if c and c.id]
                ultimo_contato_por_cliente = {}
                if cliente_ids_mov:
                    sub_ult = (
                        db.session.query(
                            Ligacao.cliente_id.label("cid"),
                            db.func.max(Ligacao.data_hora).label("ultima_data"),
                        )
                        .filter(Ligacao.cliente_id.in_(cliente_ids_mov))
                        .group_by(Ligacao.cliente_id)
                        .subquery()
                    )
                    ult_rows = (
                        db.session.query(
                            Ligacao.cliente_id,
                            Ligacao.data_hora,
                            Ligacao.resultado,
                            Usuario.nome.label("consultor_nome"),
                        )
                        .join(
                            sub_ult,
                            db.and_(
                                sub_ult.c.cid == Ligacao.cliente_id,
                                sub_ult.c.ultima_data == Ligacao.data_hora,
                            ),
                        )
                        .outerjoin(Usuario, Usuario.id == Ligacao.consultor_id)
                        .all()
                    )
                    for row in ult_rows:
                        ultimo_contato_por_cliente[int(row.cliente_id)] = {
                            "consultor_nome": str(row.consultor_nome or "").strip(),
                            "data_hora": row.data_hora,
                            "resultado": str(row.resultado or "").strip(),
                        }

                for cli in rows_mov:
                    cd = str(cli.cd_cliente_oracle or "").strip()
                    if not cd:
                        continue
                    mapa_cliente_por_codigo[cd] = cli
                    contato = ultimo_contato_por_cliente.get(int(cli.id)) if cli.id else None
                    detalhes_por_codigo[cd] = {
                        "cd_cliente": cd,
                        "cliente": str(cli.nome or "").strip(),
                        "representante": str(cli.representante_oracle or "").strip(),
                        "consultor": str(cli.categoria_consultor or "").strip(),
                        "uf": str(cli.uf or "").strip(),
                        "ultimo_contato_por": (contato.get("consultor_nome") if contato else ""),
                        "ultima_ligacao_em": (
                            contato.get("data_hora").strftime("%d/%m/%Y %H:%M")
                            if contato and contato.get("data_hora")
                            else ""
                        ),
                        "ultimo_resultado": (contato.get("resultado") if contato else ""),
                    }

            def _detalhe(cd):
                if cd in detalhes_por_codigo:
                    return detalhes_por_codigo[cd]
                return {
                    "cd_cliente": cd,
                    "cliente": "",
                    "representante": "",
                    "consultor": "",
                    "uf": "",
                    "ultimo_contato_por": "",
                    "ultima_ligacao_em": "",
                    "ultimo_resultado": "",
                }

            def _dias_sem_pedido(cd):
                cli = mapa_cliente_por_codigo.get(str(cd or "").strip())
                if not cli or not getattr(cli, "ultimo_pedido_oracle", None):
                    return None
                try:
                    return int((data_sync - cli.ultimo_pedido_oracle).days)
                except Exception:
                    return None

            def _motivo_saida_90_150(cd):
                cd = str(cd or "").strip()
                if not cd:
                    return ("Sem código do cliente", "")
                if cd in faixa_proximos_depois_sync:
                    d = _dias_sem_pedido(cd)
                    return ("Migrou para Próximos Inativação", f"{d} dias sem pedido" if d is not None else "")
                if cd in inativos_depois_sync:
                    d = _dias_sem_pedido(cd)
                    return ("Passou de 180 dias e virou Inativo", f"{d} dias sem pedido" if d is not None else "")
                cli = mapa_cliente_por_codigo.get(cd)
                if cli and not bool(getattr(cli, "ativo", True)):
                    return ("Saiu da carteira ativa do CRM", "")
                d = _dias_sem_pedido(cd)
                if d is not None and d < 90:
                    return ("Saiu por compra/recência", f"{d} dias sem pedido")
                if d is not None and d > 1095:
                    return ("Saiu da janela (>1095 dias sem pedido)", f"{d} dias sem pedido")
                return ("Mudança de base/carteira", f"{d} dias sem pedido" if d is not None else "")

            def _motivo_saida_proximos(cd):
                cd = str(cd or "").strip()
                if not cd:
                    return ("Sem código do cliente", "")
                if cd in inativos_depois_sync:
                    d = _dias_sem_pedido(cd)
                    return ("Foi para carteira de Inativos", f"{d} dias sem pedido" if d is not None else "")
                if cd in faixa_90_150_depois_sync:
                    d = _dias_sem_pedido(cd)
                    return ("Voltou para 'Sem Pedido 90-150'", f"{d} dias sem pedido" if d is not None else "")
                cli = mapa_cliente_por_codigo.get(cd)
                if cli and not bool(getattr(cli, "ativo", True)):
                    return ("Saiu da carteira ativa do CRM", "")
                d = _dias_sem_pedido(cd)
                if d is not None and d < 151:
                    return ("Saiu por compra/recência", f"{d} dias sem pedido")
                if d is not None and d > 1095:
                    return ("Saiu da janela (>1095 dias sem pedido)", f"{d} dias sem pedido")
                return ("Mudança de base/carteira", f"{d} dias sem pedido" if d is not None else "")

            def _detalhe_com_motivo(cd, motivo_fn):
                item = dict(_detalhe(cd))
                motivo, detalhe = motivo_fn(cd)
                item["motivo_movimento"] = motivo
                item["motivo_detalhe"] = detalhe
                return item

            def _motivo_entrada_inativos(cd):
                cd = str(cd or "").strip()
                if not cd:
                    return ("Sem código do cliente", "")
                d = _dias_sem_pedido(cd)
                if cd in faixa_proximos_antes_sync:
                    return ("Saiu de 'Próximos Inativação' e entrou em Inativos", f"{d} dias sem pedido" if d is not None else "")
                if cd in faixa_90_150_antes_sync:
                    return ("Avançou rápido para Inativos", f"{d} dias sem pedido" if d is not None else "")
                return ("Entrou na faixa de Inativos", f"{d} dias sem pedido" if d is not None else "")

            def _motivo_saida_inativos(cd):
                cd = str(cd or "").strip()
                if not cd:
                    return ("Sem código do cliente", "")
                cli = mapa_cliente_por_codigo.get(cd)
                if cli and not bool(getattr(cli, "ativo", True)):
                    return ("Saiu da carteira ativa do CRM", "")
                d = _dias_sem_pedido(cd)
                if d is not None and d < 181:
                    if cd in faixa_proximos_depois_sync:
                        return ("Reativou e foi para 'Próximos Inativação'", f"{d} dias sem pedido")
                    if cd in faixa_90_150_depois_sync:
                        return ("Reativou e foi para 'Sem Pedido 90-150'", f"{d} dias sem pedido")
                    return ("Saiu por compra/recência", f"{d} dias sem pedido")
                if d is not None and d > 1095:
                    return ("Saiu da janela (>1095 dias sem pedido)", f"{d} dias sem pedido")
                return ("Mudança de base/carteira", f"{d} dias sem pedido" if d is not None else "")

            salvar_movimento_inativos(
                data_ref=data_ref,
                atualizado_em=data_sync,
                entraram=[_detalhe_com_motivo(cd, _motivo_entrada_inativos) for cd in codigos_entraram],
                sairam=[_detalhe_com_motivo(cd, _motivo_saida_inativos) for cd in codigos_sairam],
                total_inativos=len(inativos_depois_sync),
            )
            salvar_movimento_carteira(
                carteira="oracle_90_150",
                data_ref=data_ref,
                atualizado_em=data_sync,
                entraram=[_detalhe(cd) for cd in codigos_90_150_entraram],
                sairam=[_detalhe_com_motivo(cd, _motivo_saida_90_150) for cd in codigos_90_150_sairam],
                total=len(faixa_90_150_depois_sync),
                codigos_presentes=sorted(faixa_90_150_depois_sync),
            )
            salvar_movimento_carteira(
                carteira="proximos_inativacao",
                data_ref=data_ref,
                atualizado_em=data_sync,
                entraram=[_detalhe(cd) for cd in codigos_proximos_entraram],
                sairam=[_detalhe_com_motivo(cd, _motivo_saida_proximos) for cd in codigos_proximos_sairam],
                total=len(faixa_proximos_depois_sync),
                codigos_presentes=sorted(faixa_proximos_depois_sync),
            )

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

            # Evita servir por horas os dados anteriores ao job.
            try:
                from routes.clientes_ligacoes.cache_invalidation import (
                    invalidar_caches_listagens_clientes,
                )

                invalidar_caches_listagens_clientes(
                    "conclusao da sincronizacao automatica oracle"
                )
                from services.warmup_service import (
                    aquecer_metricas_dependentes_oracle,
                )

                aquecer_metricas_dependentes_oracle(app)
            except Exception as cache_error:
                logger.warning(
                    "Sincronizacao concluida, mas houve falha ao renovar caches: %s",
                    cache_error,
                )

            return True
            
    except Exception as e:
        logger.error(f"\n❌ Erro na sincronização automática: {e}")
        return False

if __name__ == "__main__":
    try:
        os.environ.setdefault("DISABLE_APP_SCHEDULER", "1")
        os.environ.setdefault("DISABLE_APP_WARMUP", "1")
        from app import app as flask_app

        resultado = sincronizacao_automatica_diaria(flask_app)
        
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

