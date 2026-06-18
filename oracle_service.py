"""
Serviço de conexão e integração com banco Oracle
Para busca de dados estratégicos de clientes (CRM híbrido)
"""

import os
import logging
import threading
import inspect
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dotenv import load_dotenv
from database_utils import retry_oracle_connection, retry_database

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_DIAS_MEDIA_RECEBIMENTO_CACHE = {"ts": None, "data": {}}
_DIAS_MEDIA_RECEBIMENTO_CACHE_LOCK = threading.Lock()
_MESES_COMPRA_CACHE = {}
_MESES_COMPRA_CACHE_TTL = timedelta(hours=24)
_MESES_COMPRA_CACHE_LOCK = threading.Lock()
_PEDIDOS_ANDAMENTO_CACHE = {}
_PEDIDOS_ANDAMENTO_CACHE_TTL = timedelta(seconds=60)

# Carrega .env e .env.oracle para execuções fora do app.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(BASE_DIR, ".env.oracle"))

# Tentar importar oracledb primeiro, se não funcionar, usar cx_Oracle
try:
    import oracledb
    ORACLE_LIB = 'oracledb'
    logger.info("Usando oracledb (biblioteca moderna)")
except ImportError:
    try:
        import cx_Oracle
        ORACLE_LIB = 'cx_Oracle'
        logger.info("Usando cx_Oracle (biblioteca legada)")
    except ImportError:
        ORACLE_LIB = None
        logger.error("Nenhuma biblioteca Oracle encontrada")

class OracleService:
    """Serviço para conexão e operações com banco Oracle"""
    
    def __init__(self):
        self._pool = None
        self._pool_lock = threading.Lock()
        self._initialize_config()
    
    def _initialize_config(self):
        """Inicializa configurações de conexão Oracle"""
        self.config = {
            'user': os.getenv('ORACLE_UID', 'BAKOF'),
            'password': os.getenv('ORACLE_PWD', 'BAKOF'),
            'dsn': os.getenv('ORACLE_DBQ', 'ORCL')
        }
        
        # Configurar modo thin client (mais simples que thick client)
        if ORACLE_LIB == 'oracledb':
            try:
                lib_dir = os.getenv('ORACLE_CLIENT_LIB_DIR')
                config_dir = os.getenv('TNS_ADMIN') or os.getenv('ORACLE_CONFIG_DIR')
                init_kwargs = {}
                if lib_dir:
                    init_kwargs['lib_dir'] = lib_dir
                if config_dir:
                    init_kwargs['config_dir'] = config_dir

                if init_kwargs:
                    oracledb.init_oracle_client(**init_kwargs)
                    logger.info(f"Oracle Client inicializado (modo thick): {init_kwargs}")
                else:
                    oracledb.init_oracle_client()
                    logger.info("Oracle Client inicializado (modo thick)")
            except Exception as e:
                logger.warning(f"Nao foi possivel iniciar Oracle Client em modo thick: {e}")
                logger.info("Usando modo thin client (sem Oracle Client)")
    
    def _get_pool(self):
        """Obtém ou cria o pool de conexões Oracle (thread-safe)"""
        if self._pool is not None:
            return self._pool
        with self._pool_lock:
            if self._pool is not None:
                return self._pool
            if not ORACLE_LIB:
                raise ImportError("Nenhuma biblioteca Oracle instalada")
            try:
                if ORACLE_LIB == 'oracledb':
                    pool_kwargs = {
                        "user": self.config['user'],
                        "password": self.config['password'],
                        "dsn": self.config['dsn'],
                        "min": 2,
                        "max": 10,
                        "increment": 1,
                        "getmode": oracledb.POOL_GETMODE_WAIT,
                        "ping_interval": 60,
                    }
                    try:
                        create_pool_params = inspect.signature(oracledb.create_pool).parameters
                        if "ping_timeout" in create_pool_params:
                            pool_kwargs["ping_timeout"] = 5000
                    except Exception:
                        pass
                    self._pool = oracledb.create_pool(**pool_kwargs)
                else:  # cx_Oracle
                    self._pool = cx_Oracle.SessionPool(
                        user=self.config['user'],
                        password=self.config['password'],
                        dsn=self.config['dsn'],
                        min=2,
                        max=10,
                        increment=1,
                        getmode=cx_Oracle.SPOOL_ATTRVAL_WAIT,
                    )
                logger.info(f"Pool de conexões Oracle criado (min=2, max=10, usando {ORACLE_LIB})")
            except Exception as e:
                logger.error(f"Erro ao criar pool Oracle: {str(e)}")
                raise
        return self._pool

    @contextmanager
    def _acquire_connection(self):
        """Context manager que adquire e libera conexão do pool"""
        pool = self._get_pool()
        conn = pool.acquire()
        try:
            yield conn
        finally:
            try:
                pool.release(conn)
            except Exception:
                pass

    def test_connection(self) -> Tuple[bool, str]:
        """Testa conexão com o banco Oracle"""
        if not ORACLE_LIB:
            return False, "Nenhuma biblioteca Oracle instalada. Instale oracledb ou cx_Oracle"
        
        try:
            with self._acquire_connection() as conn:
                version = conn.version
            return True, f"Conexão bem sucedida! Oracle version: {version} (usando {ORACLE_LIB})"
        except Exception as e:
            logger.error(f"Erro inesperado ao conectar Oracle: {str(e)}")
            return False, f"Erro de conexão: {str(e)}"

    @retry_oracle_connection(max_attempts=3, delay=2.0)
    def get_connection(self):
        """Obtém conexão do pool Oracle (para compatibilidade)"""
        pool = self._get_pool()
        return pool.acquire()

    def release_connection(self, conn):
        """Libera conexão de volta ao pool"""
        if conn and self._pool:
            try:
                self._pool.release(conn)
            except Exception:
                pass
    
    def close_connection(self):
        """Fecha o pool de conexões Oracle"""
        with self._pool_lock:
            if self._pool:
                try:
                    self._pool.close(force=True)
                    self._pool = None
                    logger.info("Pool de conexões Oracle fechado")
                except Exception as e:
                    logger.error(f"Erro ao fechar pool Oracle: {str(e)}")
    
    @retry_oracle_connection(max_attempts=3, delay=2.0)
    def execute_query(self, query: str, params: Optional[Dict] = None) -> List[Dict]:
        """
        Executa query SQL e retorna resultados como lista de dicionários
        """
        cursor = None
        try:
            with self._acquire_connection() as conn:
                cursor = conn.cursor()
                
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                
                # Obter nomes das colunas
                columns = [col[0].lower() for col in cursor.description]
                
                # Converter resultados para lista de dicionários
                results = []
                for row in cursor:
                    row_dict = dict(zip(columns, row))
                    results.append(row_dict)
                
                logger.info(f"Query executada com sucesso: {len(results)} registros")
                return results
                
        except Exception as e:
            error_msg = str(e).lower()
            if any(code in error_msg for code in ['dpy-1001', 'dpi-1010', 'not connected']):
                logger.warning(f"Conexão perdida detectada ({e}), pool fará reconexão automática...")
            logger.error(f"Erro ao executar query: {str(e)}")
            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
    
    def get_clientes_alvo(self) -> List[Dict]:
        """
        Busca clientes alvo usando a query fornecida
        
        Returns:
            Lista de clientes com dados estratégicos
        """
        # REGRA VALIDADA (2026-03): 90-150 dias, 1 linha por cliente (ultimo pedido valido).
        # Qualquer alteracao aqui impacta diretamente os totais do app e do Oracle.
        query = """
        with pedidos_validos as (
            select
              PED.cd_cliente,
              PED.dt_pedido,
              PED.cd_pedido,
              PED.total_pedido,
              PED.situacao,
              PED.desc_cond_pagto,
              PED.controle,
              PED.cd_unid_de_neg,
              row_number() over (
                partition by PED.cd_cliente
                order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
              ) as rn
            from fapedido PED
            join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
            where DPA.cd_operacao_resultado_para not in ('20','21')
              and PED.controle not in ('85','96','99','86')
              and regexp_like(trim(to_char(PED.controle)), '^[0-9]+$')
              and to_number(trim(to_char(PED.controle))) between 30 and 80
              and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
        ),
        clientes_alvo as (
            select *
            from pedidos_validos
            where rn = 1
              and dt_pedido between (sysdate - 150) and (sysdate - 90)
        )
        select
          CA.dt_pedido,
          CA.cd_cliente,
          CLI.nome_completo as cliente,
          CLI.cnpj_cpf as cnpj,  -- CNPJ/CPF do cliente
          CLI.fone as telefone1,  -- Telefone 1
          CLI.fax_fone as telefone2,  -- Telefone 2
          CLI.municipio as municipio,
          CLI.uf as uf,
          CLI.contato as contato,
          CLI.cd_centralizado,
          CEN.nome_completo as nome_centralizadora,
          REP.nome_completo || ' - ' || CLI.cd_representant as representante,
          CA.total_pedido,
          CA.situacao,
          CA.desc_cond_pagto,
          CA.cd_unid_de_neg,
          coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
          case 
             when CLI.conceito = 'L' then 'LIBERADO'
             when CLI.conceito = 'B' then 'INADIMPLENTE'
             when trim(CLI.conceito) is null then 'SEM CONCEITO'
             else 'SEM CONCEITO'
          end as conceito
        from clientes_alvo CA
        join geempres CLI on CLI.cd_empresa = CA.cd_cliente
        left join Geelemen TGS on TGS.cd_tg = 634 and TGS.elemento = CLI.cd_representant
        left join Gecatego TG1 on TG1.cd_tg = 634 and TG1.categoria = coalesce(TGS.categoria,'999')
        left join geempres REP on REP.cd_empresa = CLI.cd_representant
        left join geempres CEN on CEN.cd_empresa = CLI.cd_centralizado
        where CLI.pessoa = '0'
          and CLI.tipo_de_empresa in ('R', 'T')
          and case 
                when regexp_like(TGS.categoria, '^[0-9]+$') 
                     and to_number(TGS.categoria) <= 100
                then 1
                else 0
              end = 1
        order by CA.dt_pedido asc
        """
        
        try:
            results = self.execute_query(query)
            logger.info(f"Buscados {len(results)} clientes alvo do Oracle")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar clientes alvo: {str(e)}")
            raise
    
    def get_clientes_inativos(self) -> List[Dict]:
        """
        Busca clientes inativos (181 dias a 3 anos sem pedidos) para televendas
        
        Returns:
            Lista de clientes com dados estratégicos
        """
        # REGRA VALIDADA (2026-06): inativos 181-1095 dias, 1 linha por cliente (ultimo pedido valido).
        query = """
        with pedidos_validos as (
            select
              PED.cd_cliente,
              PED.dt_pedido,
              PED.cd_pedido,
              PED.total_pedido,
              PED.situacao,
              PED.desc_cond_pagto,
              PED.controle,
              PED.cd_unid_de_neg,
              row_number() over (
                partition by PED.cd_cliente
                order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
              ) as rn
            from fapedido PED
            join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
            where DPA.cd_operacao_resultado_para not in ('20','21')
              and PED.controle not in ('85','96','99','86')
              and regexp_like(trim(to_char(PED.controle)), '^[0-9]+$')
              and to_number(trim(to_char(PED.controle))) between 30 and 80
              and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
        ),
        clientes_alvo as (
            select *
            from pedidos_validos
            where rn = 1
              and dt_pedido between (sysdate - 1095) and (sysdate - 181)
        )
        select
          CA.dt_pedido,
          CA.cd_cliente,
          CLI.nome_completo as cliente,
          CLI.cnpj_cpf as cnpj,  -- CNPJ/CPF do cliente
          CLI.fone as telefone1,  -- Telefone 1
          CLI.fax_fone as telefone2,  -- Telefone 2
          CLI.municipio as municipio,
          CLI.uf as uf,
          CLI.contato as contato,
          CLI.cd_centralizado,
          CEN.nome_completo as nome_centralizadora,
          REP.nome_completo || ' - ' || CLI.cd_representant as representante,
          CA.total_pedido,
          CA.situacao,
          CA.desc_cond_pagto,
          CA.cd_unid_de_neg,
          coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
          case 
             when CLI.conceito = 'L' then 'LIBERADO'
             when CLI.conceito = 'B' then 'INADIMPLENTE'
             when trim(CLI.conceito) is null then 'SEM CONCEITO'
             else 'SEM CONCEITO'
          end as conceito
        from clientes_alvo CA
        join geempres CLI on CLI.cd_empresa = CA.cd_cliente
        left join Geelemen TGS on TGS.cd_tg = 634 and TGS.elemento = CLI.cd_representant
        left join Gecatego TG1 on TG1.cd_tg = 634 and TG1.categoria = coalesce(TGS.categoria,'999')
        left join geempres REP on REP.cd_empresa = CLI.cd_representant
        left join geempres CEN on CEN.cd_empresa = CLI.cd_centralizado
        where CLI.pessoa = '0'
          and CLI.tipo_de_empresa in ('R', 'T')
          and case 
                when regexp_like(TGS.categoria, '^[0-9]+$') 
                     and to_number(TGS.categoria) <= 999
                then 1
                else 0
              end = 1
        order by CA.dt_pedido asc
        """
        
        try:
            results = self.execute_query(query)
            logger.info(f"Buscados {len(results)} clientes inativos do Oracle")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar clientes inativos: {str(e)}")
            raise

    def get_clientes_ativos(self) -> List[Dict]:
        """
        Busca clientes ativos (0 a 180 dias com pedido) no Oracle.

        Returns:
            Lista de clientes com dados estrategicos
        """
        query = """
        with pedidos_validos as (
            select
              PED.cd_cliente,
              PED.dt_pedido,
              PED.cd_pedido,
              PED.total_pedido,
              PED.situacao,
              PED.desc_cond_pagto,
              PED.controle,
              PED.cd_unid_de_neg,
              row_number() over (
                partition by PED.cd_cliente
                order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
              ) as rn
            from fapedido PED
            join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
            where DPA.cd_operacao_resultado_para not in ('20','21')
              and PED.controle not in ('85','96','99','86')
              and regexp_like(trim(to_char(PED.controle)), '^[0-9]+$')
              and to_number(trim(to_char(PED.controle))) between 30 and 80
              and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
        ),
        clientes_alvo as (
            select *
            from pedidos_validos
            where rn = 1
              and dt_pedido between (sysdate - 180) and sysdate
        )
        select
          CA.dt_pedido,
          CA.cd_cliente,
          CLI.nome_completo as cliente,
          CLI.cnpj_cpf as cnpj,
          CLI.fone as telefone1,
          CLI.fax_fone as telefone2,
          CLI.municipio as municipio,
          CLI.uf as uf,
          CLI.contato as contato,
          CLI.cd_centralizado,
          CEN.nome_completo as nome_centralizadora,
          REP.nome_completo || ' - ' || CLI.cd_representant as representante,
          CA.total_pedido,
          CA.situacao,
          CA.desc_cond_pagto,
          CA.cd_unid_de_neg,
          coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
          case
             when CLI.conceito = 'L' then 'LIBERADO'
             when CLI.conceito = 'B' then 'INADIMPLENTE'
             when trim(CLI.conceito) is null then 'SEM CONCEITO'
             else 'SEM CONCEITO'
          end as conceito
        from clientes_alvo CA
        join geempres CLI on CLI.cd_empresa = CA.cd_cliente
        left join Geelemen TGS on TGS.cd_tg = 634 and TGS.elemento = CLI.cd_representant
        left join Gecatego TG1 on TG1.cd_tg = 634 and TG1.categoria = coalesce(TGS.categoria,'999')
        left join geempres REP on REP.cd_empresa = CLI.cd_representant
        left join geempres CEN on CEN.cd_empresa = CLI.cd_centralizado
        where CLI.pessoa = '0'
          and CLI.tipo_de_empresa in ('R', 'T')
          and case
                when regexp_like(TGS.categoria, '^[0-9]+$')
                     and to_number(TGS.categoria) <= 999
                then 1
                else 0
              end = 1
        order by CA.dt_pedido desc
        """

        try:
            results = self.execute_query(query)
            logger.info(f"Buscados {len(results)} clientes ativos (0-180d) do Oracle")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar clientes ativos: {str(e)}")
            raise

    def get_clientes_construtoras(self) -> List[Dict]:
        """
        Busca carteira de construtoras para televendas.

        Regra: ultimo pedido valido entre 180 e 1800 dias, clientes tipo T.
        """
        query = """
        with pedidos_validos as (
            select
              PED.cd_cliente,
              PED.dt_pedido,
              PED.cd_pedido,
              PED.total_pedido,
              PED.situacao,
              PED.desc_cond_pagto,
              PED.controle,
              PED.cd_unid_de_neg,
              row_number() over (
                partition by PED.cd_cliente
                order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
              ) as rn
            from fapedido PED
            join dexpara DPA
              on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
            where DPA.cd_operacao_resultado_para not in ('20','21')
              and PED.controle not in ('85','96','99','86')
              and regexp_like(trim(to_char(PED.controle)), '^[0-9]+$')
              and to_number(trim(to_char(PED.controle))) between 30 and 80
              and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
        ),
        clientes_alvo as (
            select *
            from pedidos_validos
            where rn = 1
              and dt_pedido between (sysdate - 1800) and (sysdate - 180)
        )
        select
          CA.dt_pedido,
          CA.cd_cliente,
          CLI.nome_completo as cliente,
          CLI.cnpj_cpf as cnpj,
          CLI.fone as telefone1,
          CLI.fax_fone as telefone2,
          CLI.municipio as municipio,
          CLI.uf as uf,
          CLI.contato as contato,
          CLI.cd_centralizado,
          CEN.nome_completo as nome_centralizadora,
          REP.nome_completo || ' - ' || CLI.cd_representant as representante,
          CA.total_pedido,
          CA.situacao,
          CA.desc_cond_pagto,
          CA.cd_unid_de_neg,
          coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
          case
             when CLI.conceito = 'L' then 'LIBERADO'
             when CLI.conceito = 'B' then 'INADIMPLENTE'
             when trim(CLI.conceito) is null then 'SEM CONCEITO'
             else 'SEM CONCEITO'
          end as conceito
        from clientes_alvo CA
        join geempres CLI
          on CLI.cd_empresa = CA.cd_cliente
        left join Geelemen TGS
          on TGS.cd_tg = 634
         and TGS.elemento = CLI.cd_representant
        left join Gecatego TG1
          on TG1.cd_tg = 634
         and TG1.categoria = coalesce(TGS.categoria,'999')
        left join geempres REP
          on REP.cd_empresa = CLI.cd_representant
        left join geempres CEN
          on CEN.cd_empresa = CLI.cd_centralizado
        where CLI.pessoa = '0'
          and CLI.tipo_de_empresa = 'T'
          and case
                when regexp_like(TGS.categoria, '^[0-9]+$')
                     and to_number(TGS.categoria) <= 999
                then 1
                else 0
              end = 1
        order by CA.dt_pedido asc
        """

        try:
            results = self.execute_query(query)
            logger.info(f"Buscados {len(results)} clientes construtoras do Oracle")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar clientes construtoras: {str(e)}")
            raise

    def get_clientes_proximos_inativacao(self) -> List[Dict]:
        """
        Busca clientes próximos de inativação (151 a 180 dias sem pedidos)

        Returns:
            Lista de clientes com dados estratégicos
        """
        query = """
        with pedidos_validos as (
            select
              PED.cd_cliente,
              PED.dt_pedido,
              PED.cd_pedido,
              PED.total_pedido,
              PED.situacao,
              PED.desc_cond_pagto,
              PED.controle,
              PED.cd_unid_de_neg,
              row_number() over (
                partition by PED.cd_cliente
                order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
              ) as rn
            from fapedido PED
            join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
            where DPA.cd_operacao_resultado_para not in ('20','21')
              and PED.controle not in ('85','96','99','86')
              and regexp_like(trim(to_char(PED.controle)), '^[0-9]+$')
              and to_number(trim(to_char(PED.controle))) between 30 and 80
              and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
        ),
        clientes_alvo as (
            select *
            from pedidos_validos
            where rn = 1
              and dt_pedido between (sysdate - 180) and (sysdate - 151)
        )
        select
          CA.dt_pedido,
          CA.cd_cliente,
          CLI.nome_completo as cliente,
          CLI.cnpj_cpf as cnpj,
          CLI.fone as telefone1,
          CLI.fax_fone as telefone2,
          CLI.municipio as municipio,
          CLI.uf as uf,
          CLI.contato as contato,
          CLI.cd_centralizado,
          CEN.nome_completo as nome_centralizadora,
          REP.nome_completo || ' - ' || CLI.cd_representant as representante,
          CA.total_pedido,
          CA.situacao,
          CA.desc_cond_pagto,
          CA.cd_unid_de_neg,
          coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
          case
             when CLI.conceito = 'L' then 'LIBERADO'
             when CLI.conceito = 'B' then 'INADIMPLENTE'
             when trim(CLI.conceito) is null then 'SEM CONCEITO'
             else 'SEM CONCEITO'
          end as conceito
        from clientes_alvo CA
        join geempres CLI on CLI.cd_empresa = CA.cd_cliente
        left join Geelemen TGS on TGS.cd_tg = 634 and TGS.elemento = CLI.cd_representant
        left join Gecatego TG1 on TG1.cd_tg = 634 and TG1.categoria = coalesce(TGS.categoria,'999')
        left join geempres REP on REP.cd_empresa = CLI.cd_representant
        left join geempres CEN on CEN.cd_empresa = CLI.cd_centralizado
        where CLI.pessoa = '0'
          and CLI.tipo_de_empresa in ('R', 'T')
          and case
                when regexp_like(TGS.categoria, '^[0-9]+$')
                     and to_number(TGS.categoria) <= 100
                then 1
                else 0
              end = 1
        order by CA.dt_pedido asc
        """

        try:
            results = self.execute_query(query)
            logger.info(f"Buscados {len(results)} clientes proximos de inativacao do Oracle")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar clientes proximos de inativacao: {str(e)}")
            raise

    def get_pedidos_reativacao(self, data_inicio: datetime, data_fim: datetime,
                                dias_inatividade_inicio: int = 151,
                                dias_inatividade_fim: int = 180) -> List[Dict]:
        """
        Busca pedidos de reativação - clientes que fizeram novo pedido no período
        após X dias sem comprar (configurável).
        
        Para cada pedido novo no período, verifica se o pedido anterior do mesmo
        cliente ocorreu entre dias_inatividade_inicio e dias_inatividade_fim antes.
        
        Args:
            data_inicio: Data inicial do período de busca dos NOVOS pedidos
            data_fim: Data final do período de busca dos NOVOS pedidos
            dias_inatividade_inicio: Dias mínimos de inatividade (ex: 151)
            dias_inatividade_fim: Dias máximos de inatividade (ex: 180)
            
        Returns:
            Lista de pedidos com dados do representante e cliente
        """
        query = """
        with pedidos_validos as (
            select
              PED.cd_cliente,
              PED.dt_pedido,
              PED.cd_pedido,
              PED.total_pedido,
              PED.situacao,
              PED.desc_cond_pagto,
              PED.controle,
              PED.cd_unid_de_neg,
              row_number() over (
                partition by PED.cd_cliente
                order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
              ) as rn
            from fapedido PED
            join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
            where DPA.cd_operacao_resultado_para not in ('20','21')
              and PED.controle not in ('85','96','99','86')
              and regexp_like(trim(to_char(PED.controle)), '^[0-9]+$')
              and to_number(trim(to_char(PED.controle))) between 30 and 80
              and nvl(PED.gerou_faturamen, 0) = 1
              and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
        ),
        -- Novos pedidos feitos no período informado (rn = 1 = mais recente)
        novos_pedidos as (
            select
                cd_cliente,
                cd_pedido,
                dt_pedido as data_novo_pedido,
                total_pedido as valor_novo_pedido,
                situacao as situacao_novo_pedido,
                desc_cond_pagto as cond_pagto_novo_pedido,
                controle as controle_novo_pedido
            from pedidos_validos
            where rn = 1
              and dt_pedido >= :data_inicio
              and dt_pedido < :data_fim
        ),
        -- Pedido anterior de cada cliente (rn = 2 = segundo mais recente)
        pedido_anterior as (
            select
                cd_cliente,
                dt_pedido as data_pedido_anterior,
                total_pedido as valor_pedido_anterior,
                controle as controle_pedido_anterior
            from pedidos_validos
            where rn = 2
        ),
        -- Clientes reativados: diferença configurável entre novo pedido e anterior
        reativacoes as (
            select
                NP.cd_cliente,
                PA.data_pedido_anterior as ultimo_pedido_antigo,
                PA.valor_pedido_anterior as valor_ultimo_pedido_antigo,
                NP.cd_pedido,
                NP.data_novo_pedido,
                NP.valor_novo_pedido,
                NP.situacao_novo_pedido,
                NP.cond_pagto_novo_pedido,
                NP.controle_novo_pedido,
                PA.controle_pedido_anterior
            from novos_pedidos NP
            join pedido_anterior PA on PA.cd_cliente = NP.cd_cliente
            where NP.data_novo_pedido - PA.data_pedido_anterior between :dias_inicio and :dias_fim
        )
        select
          R.cd_cliente,
          CLI.nome_completo as nome_cliente,
          CLI.cnpj_cpf as cnpj,
          CLI.municipio,
          CLI.uf,
          CLI.contato,
          R.ultimo_pedido_antigo,
          R.valor_ultimo_pedido_antigo,
          R.cd_pedido,
          R.data_novo_pedido,
          R.valor_novo_pedido,
          R.situacao_novo_pedido,
          R.cond_pagto_novo_pedido,
          R.controle_novo_pedido,
          R.controle_pedido_anterior,
          CN.descricao as desc_controle_novo_pedido,
          CA.descricao as desc_controle_pedido_anterior,
          REP.nome_completo as nome_representante,
          CLI.cd_representant as cd_representante,
          coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
          case
             when CLI.conceito = 'L' then 'LIBERADO'
             when CLI.conceito = 'B' then 'INADIMPLENTE'
             when trim(CLI.conceito) is null then 'SEM CONCEITO'
             else 'SEM CONCEITO'
          end as conceito
        from reativacoes R
        join geempres CLI on CLI.cd_empresa = R.cd_cliente
        left join CG_VW_CONTROLE_PEDIDO CN on CN.controle = R.controle_novo_pedido
        left join CG_VW_CONTROLE_PEDIDO CA on CA.controle = R.controle_pedido_anterior
        left join Geelemen TGS on TGS.cd_tg = 634 and TGS.elemento = CLI.cd_representant
        left join Gecatego TG1 on TG1.cd_tg = 634 and TG1.categoria = coalesce(TGS.categoria,'999')
        left join geempres REP on REP.cd_empresa = CLI.cd_representant
        where CLI.pessoa = '0'
          and CLI.tipo_de_empresa in ('R', 'T')
          and CLI.conceito = 'L'
          and case
                when regexp_like(TGS.categoria, '^[0-9]+$')
                     and to_number(TGS.categoria) <= 100
                then 1
                else 0
              end = 1
        order by R.data_novo_pedido desc, R.cd_cliente
        """
        
        try:
            results = self.execute_query(query, {
                'data_inicio': data_inicio,
                'data_fim': data_fim,
                'dias_inicio': dias_inatividade_inicio,
                'dias_fim': dias_inatividade_fim
            })
            logger.info(f"Buscados {len(results)} pedidos de reativacao no periodo {data_inicio} a {data_fim} (janela {dias_inatividade_inicio}-{dias_inatividade_fim} dias)")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar pedidos de reativacao: {str(e)}")
            raise

    def get_resumo_pedidos_cliente(self, cd_cliente: str, janela_dias: int = 365, modo_especial: bool = False) -> List[Dict]:
        """
        Busca resumo de pedidos de um cliente específico
        
        Args:
            cd_cliente: Código do cliente no Oracle
            
        Returns:
            Lista com histórico de pedidos
        """
        dias = int(janela_dias or 365)
        if dias < 30:
            dias = 30
        if dias > 1800:
            dias = 1800

        # Em modo especial (detalhamento de cliente), nao limitar a faturados
        # para evitar "sumir" pedido valido no historico exibido.
        filtro_faturamento = (
            ""
            if modo_especial
            else (
                "and nvl(PED.gerou_faturamen, 0) = 1 "
                "and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')"
            )
        )

        query = f"""
        select 
            PED.cd_pedido,
            PED.dt_pedido,
            PED.total_pedido,
            PED.situacao,
            PED.desc_cond_pagto,
            PED.controle,
            CTL.descricao as desc_controle,
            PED.cd_unid_de_neg
        from fapedido PED
        left join CG_VW_CONTROLE_PEDIDO CTL on CTL.controle = PED.controle
        where PED.cd_cliente = :cd_cliente
          and PED.dt_pedido >= (sysdate - :janela_dias)
          {filtro_faturamento}
        order by PED.dt_pedido desc
        """
        
        try:
            results = self.execute_query(query, {'cd_cliente': cd_cliente, 'janela_dias': dias})
            logger.info(f"Buscados {len(results)} pedidos para cliente {cd_cliente}")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar pedidos do cliente {cd_cliente}: {str(e)}")
            raise

    def get_resumo_pedidos_cliente_periodo(
        self,
        cd_cliente: str,
        data_inicio: datetime,
        data_fim: datetime,
        modo_especial: bool = False,
    ) -> List[Dict]:
        """
        Busca pedidos de um cliente em um periodo fechado (inicio <= dt_pedido < fim).
        """
        filtro_faturamento = (
            ""
            if modo_especial
            else (
                "and nvl(PED.gerou_faturamen, 0) = 1 "
                "and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')"
            )
        )

        query = f"""
        select
            PED.cd_pedido,
            PED.dt_pedido,
            PED.total_pedido,
            PED.situacao,
            PED.desc_cond_pagto,
            PED.controle,
            CTL.descricao as desc_controle,
            PED.cd_unid_de_neg
        from fapedido PED
        left join CG_VW_CONTROLE_PEDIDO CTL on CTL.controle = PED.controle
        where PED.cd_cliente = :cd_cliente
          and PED.dt_pedido >= :data_inicio
          and PED.dt_pedido < :data_fim
          {filtro_faturamento}
        order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
        """
        params = {
            "cd_cliente": cd_cliente,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
        }
        try:
            results = self.execute_query(query, params)
            logger.info(
                "Buscados %s pedidos para cliente %s no periodo %s -> %s",
                len(results),
                cd_cliente,
                data_inicio.strftime("%Y-%m-%d"),
                data_fim.strftime("%Y-%m-%d"),
            )
            return results
        except Exception as e:
            logger.error(
                f"Erro ao buscar pedidos do cliente {cd_cliente} no periodo: {str(e)}"
            )
            raise
    
    def get_itens_pedido_oracle(self, cd_cliente: str, janela_dias: int = 365, modo_especial: bool = False) -> List[Dict]:
        """
        Busca itens dos pedidos de um cliente específico com nomes dos produtos
        
        Args:
            cd_cliente: Código do cliente no Oracle
            
        Returns:
            Lista com itens dos pedidos incluindo nomes dos produtos
        """
        dias = int(janela_dias or 365)
        if dias < 30:
            dias = 30
        if dias > 1800:
            dias = 1800

        filtro_faturamento = (
            ""
            if modo_especial
            else (
                "AND nvl(p.gerou_faturamen, 0) = 1 "
                "AND upper(trim(nvl(to_char(p.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')"
            )
        )

        query = f"""
        SELECT 
            i.cd_material as idproduto,
            i.quantidade,
            i.pr_unitario as precounitario,
            i.pe_desconto as pe_desconto,
            i.vl_total_item_l as valorliquidoitem,
            i.sequencia as ordenacao,
            i.dt_item,
            p.dt_pedido,
            p.cd_pedido,
            p.situacao as situacao,
            COALESCE(m.descricao, i.cd_material) as nome_produto,
            m.peso as peso
        FROM fapedido p
        JOIN FAITEMPE i ON i.cd_pedido = p.cd_pedido
        LEFT JOIN esmateri m ON m.cd_material = i.cd_material
        WHERE p.cd_cliente = :cd_cliente
          AND p.dt_pedido between (sysdate - :janela_dias) and sysdate
          {filtro_faturamento}
          AND i.ROWID = (
            SELECT MIN(i2.ROWID)
            FROM FAITEMPE i2
            WHERE i2.cd_pedido = i.cd_pedido
              AND i2.cd_material = i.cd_material
              AND i2.sequencia = i.sequencia
          )
        ORDER BY p.dt_pedido DESC, i.sequencia
        """
        
        try:
            results = self.execute_query(query, {'cd_cliente': cd_cliente, 'janela_dias': dias})
            logger.info(f"Buscados {len(results)} itens para cliente {cd_cliente}")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar itens do cliente {cd_cliente}: {str(e)}")
            raise

    def get_valor_total_365dias(self, cd_cliente: str) -> float:
        """Busca valor total dos pedidos dos últimos 365 dias de um cliente"""
        query = """
        SELECT COALESCE(SUM(p.total_pedido), 0) as valor_total_365dias
        FROM fapedido p
        WHERE p.cd_cliente = :cd_cliente
          AND p.dt_pedido >= ADD_MONTHS(SYSDATE, -12)
          AND upper(trim(nvl(to_char(p.situacao), ''))) = 'F'
          AND nvl(p.gerou_faturamen, 0) = 1
          AND upper(trim(nvl(to_char(p.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
        """
        
        try:
            results = self.execute_query(query, {'cd_cliente': cd_cliente})
            valor_total = float(results[0]['valor_total_365dias']) if results else 0.0
            logger.info(f"Valor total 365 dias para cliente {cd_cliente}: R$ {valor_total:.2f}")
            return valor_total
        except Exception as e:
            logger.error(f"Erro ao buscar valor total 365 dias do cliente {cd_cliente}: {str(e)}")
            return 0.0

    def get_quantidade_pedidos_365dias(
        self,
        codigos_clientes: List[str],
        periodo: str = "ano_atual",
    ) -> Dict[str, int]:
        """Busca a quantidade de pedidos faturados por cliente no periodo escolhido."""
        def _canon_cd(valor):
            txt = str(valor or "").strip()
            if not txt:
                return ""
            dig = "".join(ch for ch in txt if ch.isdigit())
            if dig:
                try:
                    return str(int(dig))
                except Exception:
                    return dig
            return txt.upper()

        codigos = []
        vistos = set()
        for codigo in codigos_clientes or []:
            cd = str(codigo or "").strip()
            if not cd or cd in vistos:
                continue
            vistos.add(cd)
            codigos.append(cd)

        if not codigos:
            return {}

        periodo_norm = str(periodo or "ano_atual").strip().lower()
        filtro_periodo_sql = "TRUNC(SYSDATE, 'YYYY')"
        if periodo_norm == "ultimos_365_dias":
            filtro_periodo_sql = "SYSDATE - 365"
        elif periodo_norm == "ultimos_2_anos":
            filtro_periodo_sql = "SYSDATE - 730"

        resultado = {}
        tamanho_lote = 900
        for inicio in range(0, len(codigos), tamanho_lote):
            lote = codigos[inicio:inicio + tamanho_lote]
            params = {}
            binds = []
            for idx, codigo in enumerate(lote):
                chave = f"cd_{idx}"
                binds.append(f":{chave}")
                params[chave] = codigo

            query = f"""
            SELECT
                trim(to_char(p.cd_cliente)) as cd_cliente,
                count(distinct p.cd_pedido) as qtd_pedidos_365d
            FROM fapedido p
            WHERE trim(to_char(p.cd_cliente)) in ({", ".join(binds)})
              AND p.dt_pedido >= {filtro_periodo_sql}
              AND upper(trim(nvl(to_char(p.situacao), ''))) = 'F'
              AND nvl(p.gerou_faturamen, 0) = 1
              AND upper(trim(nvl(to_char(p.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
            GROUP BY trim(to_char(p.cd_cliente))
            """

            try:
                rows = self.execute_query(query, params)
            except Exception as e:
                logger.warning(f"Falha ao buscar quantidade de pedidos 365d: {e}")
                return {}

            for row in rows:
                cd = str(row.get("cd_cliente") or "").strip()
                if not cd:
                    continue
                try:
                    qtd = int(row.get("qtd_pedidos_365d") or 0)
                except Exception:
                    qtd = 0
                resultado[cd] = qtd
                cd_canon = _canon_cd(cd)
                if cd_canon and cd_canon not in resultado:
                    resultado[cd_canon] = qtd

        for codigo in codigos:
            resultado.setdefault(codigo, 0)
            cd_canon = _canon_cd(codigo)
            if cd_canon:
                resultado.setdefault(cd_canon, resultado.get(codigo, 0))
        return resultado

    def get_meses_compra_por_cliente(
        self,
        codigos_clientes: List[str],
        periodo: str = "ano_atual",
    ) -> Dict[str, List[tuple[int, int]]]:
        """Busca os meses com compra faturada por cliente no periodo escolhido."""
        def _canon_cd(valor):
            txt = str(valor or "").strip()
            if not txt:
                return ""
            dig = "".join(ch for ch in txt if ch.isdigit())
            if dig:
                try:
                    return str(int(dig))
                except Exception:
                    return dig
            return txt.upper()

        codigos = []
        vistos = set()
        for codigo in codigos_clientes or []:
            cd = str(codigo or "").strip()
            if not cd or cd in vistos:
                continue
            vistos.add(cd)
            codigos.append(cd)

        if not codigos:
            return {}

        periodo_norm = str(periodo or "ano_atual").strip().lower()
        filtro_periodo_sql = "TRUNC(SYSDATE, 'YYYY')"
        if periodo_norm == "ultimos_365_dias":
            filtro_periodo_sql = "SYSDATE - 365"
        elif periodo_norm == "ultimos_2_anos":
            filtro_periodo_sql = "SYSDATE - 730"

        resultado = {cd: [] for cd in codigos}
        tamanho_lote = 900
        for inicio in range(0, len(codigos), tamanho_lote):
            lote = codigos[inicio:inicio + tamanho_lote]
            params = {}
            binds = []
            for idx, codigo in enumerate(lote):
                chave = f"cd_{idx}"
                binds.append(f":{chave}")
                params[chave] = codigo

            query = f"""
            SELECT
                trim(to_char(p.cd_cliente)) as cd_cliente,
                EXTRACT(YEAR FROM p.dt_pedido) as ano_pedido,
                EXTRACT(MONTH FROM p.dt_pedido) as mes_pedido
            FROM fapedido p
            WHERE trim(to_char(p.cd_cliente)) in ({", ".join(binds)})
              AND p.dt_pedido >= {filtro_periodo_sql}
              AND upper(trim(nvl(to_char(p.situacao), ''))) = 'F'
              AND nvl(p.gerou_faturamen, 0) = 1
              AND upper(trim(nvl(to_char(p.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
            GROUP BY
                trim(to_char(p.cd_cliente)),
                EXTRACT(YEAR FROM p.dt_pedido),
                EXTRACT(MONTH FROM p.dt_pedido)
            """

            try:
                rows = self.execute_query(query, params)
            except Exception as e:
                logger.warning(f"Falha ao buscar meses de compra por cliente: {e}")
                return {}

            for row in rows:
                cd = str(row.get("cd_cliente") or "").strip()
                if not cd:
                    continue
                try:
                    ano = int(row.get("ano_pedido") or 0)
                    mes = int(row.get("mes_pedido") or 0)
                except Exception:
                    continue
                if ano <= 0 or mes <= 0 or mes > 12:
                    continue
                resultado.setdefault(cd, []).append((ano, mes))
                cd_canon = _canon_cd(cd)
                if cd_canon:
                    resultado.setdefault(cd_canon, []).append((ano, mes))

        for chave, meses in list(resultado.items()):
            if not meses:
                continue
            unicos = sorted(set(meses))
            resultado[chave] = unicos
        return resultado

    def get_pedidos_em_andamento_recentes(
        self,
        codigos_clientes: List[str],
        dias_recencia: int = 45,
    ) -> Dict[str, Dict]:
        """Busca o pedido recente em andamento por cliente para sinalizacao visual."""
        codigos = []
        vistos = set()
        for codigo in codigos_clientes or []:
            cd = str(codigo or "").strip()
            if not cd or cd in vistos:
                continue
            vistos.add(cd)
            codigos.append(cd)
        if not codigos:
            return {}

        def _canon_cd(valor):
            txt = str(valor or "").strip()
            if not txt:
                return ""
            dig = "".join(ch for ch in txt if ch.isdigit())
            if dig:
                try:
                    return str(int(dig))
                except Exception:
                    return dig
            return txt.upper()

        def _buscar_lote(codigos_lote):
            params = {"dias_recencia": dias_recencia}
            binds = []
            for idx, codigo in enumerate(codigos_lote):
                chave = f"cd_{idx}"
                binds.append(f":{chave}")
                params[chave] = codigo

            query = f"""
            with pedidos_andamento as (
                select
                  trim(to_char(PED.cd_cliente)) as cd_cliente,
                  PED.cd_pedido,
                  PED.dt_pedido,
                  PED.total_pedido,
                  PED.situacao,
                  PED.controle,
                  PED.desc_cond_pagto,
                  CTL.descricao as desc_controle,
                  row_number() over (
                    partition by PED.cd_cliente
                    order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
                  ) as rn
                from fapedido PED
                join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
                left join CG_VW_CONTROLE_PEDIDO CTL on CTL.controle = PED.controle
                where trim(to_char(PED.cd_cliente)) in ({", ".join(binds)})
                  and DPA.cd_operacao_resultado_para not in ('20','21')
                  and PED.controle not in ('85','96','99','86')
                  and regexp_like(trim(to_char(PED.controle)), '^[0-9]+$')
                  and to_number(trim(to_char(PED.controle))) between 30 and 80
                  and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
                  and (
                        nvl(PED.gerou_faturamen, 0) = 0
                        or upper(trim(nvl(to_char(PED.situacao), ''))) <> 'F'
                      )
                  and PED.dt_pedido >= (sysdate - :dias_recencia)
            )
            select
              cd_cliente,
              cd_pedido,
              dt_pedido,
              total_pedido,
              situacao,
              controle,
              desc_cond_pagto,
              desc_controle
            from pedidos_andamento
            where rn = 1
            """
            return self.execute_query(query, params)

        dias_recencia = max(1, min(int(dias_recencia or 45), 120))
        rows = []
        try:
            for inicio in range(0, len(codigos), 900):
                rows.extend(_buscar_lote(codigos[inicio:inicio + 900]))
        except Exception as e:
            logger.warning(f"Falha ao buscar pedidos em andamento recentes: {e}")
            return {}

        resultado = {}
        for row in rows:
            cd = str(row.get("cd_cliente") or "").strip()
            if not cd:
                continue
            payload = {
                "cd_cliente": cd,
                "cd_pedido": row.get("cd_pedido"),
                "dt_pedido": row.get("dt_pedido"),
                "total_pedido": row.get("total_pedido"),
                "situacao": row.get("situacao"),
                "controle": row.get("controle"),
                "desc_cond_pagto": row.get("desc_cond_pagto"),
                "desc_controle": row.get("desc_controle"),
            }
            resultado[cd] = payload
            cd_canon = _canon_cd(cd)
            if cd_canon and cd_canon not in resultado:
                resultado[cd_canon] = payload
        return resultado

    def get_centralizadora_cliente(self, cd_cliente: str) -> Dict:
        """
        Busca dados da centralizadora de um cliente no Oracle.

        Returns:
            Dict com cd_centralizado e nome_centralizadora (ou vazio se nao houver)
        """
        query = """
        SELECT
            cli.cd_empresa as cd_cliente,
            cli.cd_centralizado,
            cen.nome_completo as nome_centralizadora
        FROM geempres cli
        LEFT JOIN geempres cen
               ON cen.cd_empresa = cli.cd_centralizado
        WHERE cli.cd_empresa = :cd_cliente
        """

        try:
            results = self.execute_query(query, {'cd_cliente': cd_cliente})
            if not results:
                return {}
            row = results[0]
            return {
                'cd_centralizado': row.get('cd_centralizado'),
                'nome_centralizadora': row.get('nome_centralizadora')
            }
        except Exception as e:
            logger.error(f"Erro ao buscar centralizadora do cliente {cd_cliente}: {str(e)}")
            return {}

    def get_cliente_por_cnpj(self, cnpj: str) -> Optional[Dict]:
        """
        Busca um cliente Oracle por CNPJ (somente digitos) e retorna dados principais
        para pre-preenchimento de cadastro manual.
        Se não encontrar pelo CNPJ completo, tenta buscar pelo CNPJ Raiz (8 primeiros dígitos).
        """
        cnpj_digits = "".join(ch for ch in str(cnpj or "") if ch.isdigit())
        if len(cnpj_digits) < 7:
            return None

        query = """
        with pedidos_validos as (
            select
              PED.cd_cliente,
              PED.dt_pedido,
              PED.cd_pedido,
              PED.total_pedido,
              PED.situacao,
              PED.desc_cond_pagto,
              row_number() over (
                partition by PED.cd_cliente
                order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
              ) as rn
            from fapedido PED
            join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
            where DPA.cd_operacao_resultado_para not in ('20','21')
              and PED.controle not in ('85','96','99','86')
              and upper(trim(nvl(to_char(PED.situacao), ''))) = 'F'
              and nvl(PED.gerou_faturamen, 0) = 1
              and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
        ),
        ultimo_pedido as (
            select *
            from pedidos_validos
            where rn = 1
        )
        select
          CLI.cd_empresa as cd_cliente,
          CLI.nome_completo as cliente,
          CLI.cnpj_cpf as cnpj,
          CLI.fone as telefone1,
          CLI.fax_fone as telefone2,
          CLI.municipio as municipio,
          CLI.uf as uf,
          CLI.contato as contato,
          REP.nome_completo || ' - ' || CLI.cd_representant as representante,
          coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
          UPE.dt_pedido,
          UPE.total_pedido,
          UPE.situacao,
          UPE.desc_cond_pagto,
          case
             when CLI.conceito = 'L' then 'LIBERADO'
             when CLI.conceito = 'B' then 'INADIMPLENTE'
             when trim(CLI.conceito) is null then 'SEM CONCEITO'
             else 'SEM CONCEITO'
          end as conceito
        from geempres CLI
        left join ultimo_pedido UPE on UPE.cd_cliente = CLI.cd_empresa
        left join Geelemen TGS on TGS.cd_tg = 634 and TGS.elemento = CLI.cd_representant
        left join Gecatego TG1 on TG1.cd_tg = 634 and TG1.categoria = coalesce(TGS.categoria,'999')
        left join geempres REP on REP.cd_empresa = CLI.cd_representant
        where regexp_replace(nvl(to_char(CLI.cnpj_cpf), ''), '[^0-9]', '') = :cnpj
          and CLI.pessoa = '0'
          and CLI.tipo_de_empresa in ('R', 'T')
        order by UPE.dt_pedido desc nulls last, CLI.cd_empresa
        fetch first 1 rows only
        """

        try:
            # Se recebeu 7 ou 8 dígitos, é CNPJ Raiz - pular direto para busca por raiz
            if len(cnpj_digits) in (7, 8):
                # Se tiver 7 dígitos, completa com zero à ESQUERDA (não à direita)
                if len(cnpj_digits) == 7:
                    cnpj_raiz = '0' + cnpj_digits
                else:
                    cnpj_raiz = cnpj_digits
                logger.info(f"CNPJ Raiz detectado ({cnpj_digits} -> {cnpj_raiz}). Buscando por SUBSTR...")
                
                # Query modificada para buscar usando SUBSTR nos primeiros 8 dígitos
                query_raiz = """
                with pedidos_validos as (
                    select
                      PED.cd_cliente,
                      PED.dt_pedido,
                      PED.cd_pedido,
                      PED.total_pedido,
                      PED.situacao,
                      PED.desc_cond_pagto,
                      row_number() over (
                        partition by PED.cd_cliente
                        order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
                      ) as rn
                    from fapedido PED
                    join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
                    where DPA.cd_operacao_resultado_para not in ('20','21')
                      and PED.controle not in ('85','96','99','86')
                      and upper(trim(nvl(to_char(PED.situacao), ''))) = 'F'
                      and nvl(PED.gerou_faturamen, 0) = 1
                      and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
                ),
                ultimo_pedido as (
                    select *
                    from pedidos_validos
                    where rn = 1
                )
                select
                  CLI.cd_empresa as cd_cliente,
                  CLI.nome_completo as cliente,
                  CLI.cnpj_cpf as cnpj,
                  CLI.fone as telefone1,
                  CLI.fax_fone as telefone2,
                  CLI.municipio as municipio,
                  CLI.uf as uf,
                  CLI.contato as contato,
                  REP.nome_completo || ' - ' || CLI.cd_representant as representante,
                  coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
                  UPE.dt_pedido,
                  UPE.total_pedido,
                  UPE.situacao,
                  UPE.desc_cond_pagto,
                  case
                     when CLI.conceito = 'L' then 'LIBERADO'
                     when CLI.conceito = 'B' then 'INADIMPLENTE'
                     when trim(CLI.conceito) is null then 'SEM CONCEITO'
                     else 'SEM CONCEITO'
                  end as conceito
                from geempres CLI
                left join ultimo_pedido UPE on UPE.cd_cliente = CLI.cd_empresa
                left join Geelemen TGS on TGS.cd_tg = 634 and TGS.elemento = CLI.cd_representant
                left join Gecatego TG1 on TG1.cd_tg = 634 and TG1.categoria = coalesce(TGS.categoria,'999')
                left join geempres REP on REP.cd_empresa = CLI.cd_representant
                where SUBSTR(regexp_replace(nvl(to_char(CLI.cnpj_cpf), ''), '[^0-9]', ''), 1, 8) = :cnpj_raiz
                  and CLI.pessoa = '0'
                  and CLI.tipo_de_empresa in ('R', 'T')
                order by UPE.dt_pedido desc nulls last, CLI.cd_empresa
                fetch first 1 rows only
                """
                
                results_raiz = self.execute_query(query_raiz, {"cnpj_raiz": cnpj_raiz})
                if results_raiz:
                    logger.info(f"Cliente encontrado via CNPJ Raiz: {cnpj_raiz}")
                    return results_raiz[0]
                return None
            
            # Se tem mais de 8 dígitos, tentar primeiro pelo CNPJ completo
            results = self.execute_query(query, {"cnpj": cnpj_digits})
            if results:
                logger.info(f"Cliente encontrado via CNPJ completo: {cnpj_digits[:4]}...{cnpj_digits[-4:]}")
                return results[0]
            
            logger.info(f"CNPJ completo {cnpj_digits} não encontrado. Tentando fallback por CNPJ Raiz...")
            
            # Se não encontrou pelo CNPJ completo, tentar pelo CNPJ Raiz (fallback)
            cnpj_raiz = cnpj_digits[:8]
            logger.info(f"Tentando busca por CNPJ Raiz: {cnpj_raiz}")
            
            query_raiz = """
            with pedidos_validos as (
                select
                  PED.cd_cliente,
                  PED.dt_pedido,
                  PED.cd_pedido,
                  PED.total_pedido,
                  PED.situacao,
                  PED.desc_cond_pagto,
                  row_number() over (
                    partition by PED.cd_cliente
                    order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
                  ) as rn
                from fapedido PED
                join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
                where DPA.cd_operacao_resultado_para not in ('20','21')
                  and PED.controle not in ('85','96','99','86')
                  and upper(trim(nvl(to_char(PED.situacao), ''))) = 'F'
                  and nvl(PED.gerou_faturamen, 0) = 1
                  and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
            ),
            ultimo_pedido as (
                select *
                from pedidos_validos
                where rn = 1
            )
            select
              CLI.cd_empresa as cd_cliente,
              CLI.nome_completo as cliente,
              CLI.cnpj_cpf as cnpj,
              CLI.fone as telefone1,
              CLI.fax_fone as telefone2,
              CLI.municipio as municipio,
              CLI.uf as uf,
              CLI.contato as contato,
              REP.nome_completo || ' - ' || CLI.cd_representant as representante,
              coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
              UPE.dt_pedido,
              UPE.total_pedido,
              UPE.situacao,
              UPE.desc_cond_pagto,
              case
                 when CLI.conceito = 'L' then 'LIBERADO'
                 when CLI.conceito = 'B' then 'INADIMPLENTE'
                 when trim(CLI.conceito) is null then 'SEM CONCEITO'
                 else 'SEM CONCEITO'
              end as conceito
            from geempres CLI
            left join ultimo_pedido UPE on UPE.cd_cliente = CLI.cd_empresa
            left join Geelemen TGS on TGS.cd_tg = 634 and TGS.elemento = CLI.cd_representant
            left join Gecatego TG1 on TG1.cd_tg = 634 and TG1.categoria = coalesce(TGS.categoria,'999')
            left join geempres REP on REP.cd_empresa = CLI.cd_representant
            where SUBSTR(regexp_replace(nvl(to_char(CLI.cnpj_cpf), ''), '[^0-9]', ''), 1, 8) = :cnpj_raiz
              and CLI.pessoa = '0'
              and CLI.tipo_de_empresa in ('R', 'T')
            order by UPE.dt_pedido desc nulls last, CLI.cd_empresa
            fetch first 1 rows only
            """
            
            results_raiz = self.execute_query(query_raiz, {"cnpj_raiz": cnpj_raiz})
            if results_raiz:
                logger.info(f"Cliente encontrado via CNPJ Raiz: {cnpj_raiz}")
                return results_raiz[0]

            # Último recurso: busca simples apenas na tabela geempres sem filtros de pedidos
            logger.info(f"Tentando busca simples na geempres para CNPJ: {cnpj_digits}")
            query_simples = """
            select
              CLI.cd_empresa as cd_cliente,
              CLI.nome_completo as cliente,
              CLI.cnpj_cpf as cnpj,
              CLI.fone as telefone1,
              CLI.fax_fone as telefone2,
              CLI.municipio as municipio,
              CLI.uf as uf,
              CLI.contato as contato,
              REP.nome_completo || ' - ' || CLI.cd_representant as representante,
              coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
              case
                 when CLI.conceito = 'L' then 'LIBERADO'
                 when CLI.conceito = 'B' then 'INADIMPLENTE'
                 when trim(CLI.conceito) is null then 'SEM CONCEITO'
                 else 'SEM CONCEITO'
              end as conceito
            from geempres CLI
            left join Geelemen TGS on TGS.cd_tg = 634 and TGS.elemento = CLI.cd_representant
            left join Gecatego TG1 on TG1.cd_tg = 634 and TG1.categoria = coalesce(TGS.categoria,'999')
            left join geempres REP on REP.cd_empresa = CLI.cd_representant
            where regexp_replace(nvl(to_char(CLI.cnpj_cpf), ''), '[^0-9]', '') = :cnpj
              and CLI.pessoa = '0'
              and CLI.tipo_de_empresa in ('R', 'T')
            fetch first 1 rows only
            """
            results_simples = self.execute_query(query_simples, {"cnpj": cnpj_digits})
            if results_simples:
                logger.info(f"Cliente encontrado via busca simples: {cnpj_digits[:4]}...{cnpj_digits[-4:]}")
                return results_simples[0]

            logger.warning(f"Cliente NÃO ENCONTRADO - CNPJ: {cnpj_digits} (raiz: {cnpj_raiz})")
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar cliente por CNPJ no Oracle ({cnpj_digits}): {str(e)}")
            return None

    def get_cliente_por_codigo(self, cd_cliente: str) -> Optional[Dict]:
        """
        Busca um cliente Oracle por codigo e retorna dados principais para detalhes.
        """
        cd_limpo = str(cd_cliente or "").strip()
        if not cd_limpo:
            return None

        query = """
        with pedidos_validos as (
            select
              PED.cd_cliente,
              PED.dt_pedido,
              PED.cd_pedido,
              PED.total_pedido,
              PED.situacao,
              PED.desc_cond_pagto,
              row_number() over (
                partition by PED.cd_cliente
                order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
              ) as rn
            from fapedido PED
            join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
            where DPA.cd_operacao_resultado_para not in ('20','21')
              and PED.controle not in ('85','96','99','86')
              and upper(trim(nvl(to_char(PED.situacao), ''))) = 'F'
              and nvl(PED.gerou_faturamen, 0) = 1
              and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
        ),
        ultimo_pedido as (
            select *
            from pedidos_validos
            where rn = 1
        )
        select
          CLI.cd_empresa as cd_cliente,
          CLI.nome_completo as cliente,
          CLI.cnpj_cpf as cnpj,
          CLI.fone as telefone1,
          CLI.fax_fone as telefone2,
          CLI.municipio as municipio,
          CLI.uf as uf,
          CLI.contato as contato,
          REP.nome_completo || ' - ' || CLI.cd_representant as representante,
          coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
          UPE.dt_pedido,
          UPE.total_pedido,
          UPE.situacao,
          UPE.desc_cond_pagto,
          case
             when CLI.conceito = 'L' then 'LIBERADO'
             when CLI.conceito = 'B' then 'INADIMPLENTE'
             when trim(CLI.conceito) is null then 'SEM CONCEITO'
             else 'SEM CONCEITO'
          end as conceito
        from geempres CLI
        left join ultimo_pedido UPE on UPE.cd_cliente = CLI.cd_empresa
        left join Geelemen TGS on TGS.cd_tg = 634 and TGS.elemento = CLI.cd_representant
        left join Gecatego TG1 on TG1.cd_tg = 634 and TG1.categoria = coalesce(TGS.categoria,'999')
        left join geempres REP on REP.cd_empresa = CLI.cd_representant
        where to_char(CLI.cd_empresa) = :cd_cliente
          and CLI.pessoa = '0'
          and CLI.tipo_de_empresa in ('R', 'T')
        fetch first 1 rows only
        """

        try:
            results = self.execute_query(query, {"cd_cliente": cd_limpo})
            if not results:
                return None
            return results[0]
        except Exception as e:
            logger.error(f"Erro ao buscar cliente por codigo no Oracle ({cd_limpo}): {str(e)}")
            return None

    def get_vinculos_supervisor_representante(self, codigo_supervisor: Optional[str] = None) -> List[Dict]:
        """
        Busca vínculos entre supervisores e representantes na TG 650.
        
        Args:
            codigo_supervisor: Código do supervisor para filtrar (opcional)
            
        Returns:
            Lista de vínculos com estrutura: {
                'categoria': código do supervisor,
                'elemento': código do representante,
                'desc_categoria': descrição do supervisor (se disponível),
                'nome_representante': nome do representante
            }
        """
        query = """
        SELECT 
            TG650.categoria,
            TG650.elemento as cd_representante,
            CAT.desc_categoria,
            REP.nome_completo as nome_representante
        FROM Geelemen TG650
        LEFT JOIN Gecatego CAT ON CAT.cd_tg = 650 AND CAT.categoria = TG650.categoria
        LEFT JOIN geempres REP ON REP.cd_empresa = TG650.elemento
        WHERE TG650.cd_tg = 650
        """
        
        params = {}
        if codigo_supervisor:
            query += " AND TG650.categoria = :codigo_supervisor"
            params['codigo_supervisor'] = str(codigo_supervisor).strip()
        
        query += " ORDER BY TG650.categoria, TG650.elemento"
        
        try:
            results = self.execute_query(query, params if params else None)
            logger.info(f"Buscados {len(results)} vínculos supervisor-representante da TG 650")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar vínculos TG 650: {str(e)}")
            raise

    def get_vendas_mes_representantes(
        self,
        ano: int,
        mes: int,
        dia_corte: Optional[int] = None,
    ) -> List[Dict]:
        """
        Retorna vendas faturadas por representante em um mês/ano.
        Se dia_corte for informado, filtra apenas até aquele dia do mês.
        Retorna lista de dicts com: cd_representante, nome_representante, total_vendas.
        Somente leitura — não altera nenhum dado no Oracle.
        """
        data_inicio = f"01/{mes:02d}/{ano}"
        if dia_corte:
            data_fim = f"{dia_corte:02d}/{mes:02d}/{ano}"
        else:
            from calendar import monthrange
            ultimo_dia = monthrange(ano, mes)[1]
            data_fim = f"{ultimo_dia:02d}/{mes:02d}/{ano}"

        query = f"""
        SELECT
            base.cd_representante,
            base.nome_representante,
            base.categoria_consultor,
            SUM(base.receita_resultado) AS total_vendas
        FROM (
            select to_char(Mov.dt_item,'YYYY') Ano,case to_char(Mov.dt_item,'MM') when '01' Then 'Janeiro'
            when '02' Then 'Fevereiro'
            when '03' Then 'Março'
            when '04' Then 'Abril'
            when '05' Then 'Maio'
            when '06' Then 'Junho'
            when '07' Then 'Julho'
            when '08' Then 'Agosto'
            when '09' Then 'Setembro'
            when '10' Then 'Outubro'
            when '11' Then 'Novembro'
            when '12' Then 'Dezembro' Else 'Outros' End Mês,Mov.dt_item Data,mov.cd_unid_de_neg||' - '||Uni.fantasia Unidade,Mat.cd_sub_grupo||' - '||Sub.Descricao Segmento,ped.cd_representant cd_representante,Ven.Nome_Completo nome_representante,NVL(Elem.Categoria,'ZZZ')||' - '||NVL(Cat.Desc_categoria,'Sem Consultor Destinado') categoria_consultor,
            'Pedido: '||Mov.cd_pedido||' Sequencia: '||cast(Mov.sequencia as char(5)) Documento,Mov.vl_total_item_l Receita_Resultado,'Pedidos' Tipo
            from faitempe Mov,Geunidne Uni,Esmateri Mat,essubgru Sub,dexpara dpa,fapedido Ped
            Left Outer Join geelemen Elem On Elem.cd_tg=634 and Elem.elemento=Ped.cd_representant
            Left Outer Join gecatego Cat On Cat.cd_tg=634 and Cat.Categoria=Elem.Categoria
            ,Geempres Ven
            where cd_especie='R' and Mov.controle not in ('00','85','96','99','86')
            --and mov.situacao<>' '
            and mov.dt_item>=TO_DATE('{data_inicio}', 'DD/MM/YYYY')
            and mov.dt_item<=TO_DATE('{data_fim}', 'DD/MM/YYYY')
            and uni.cd_unidade_de_n=mov.cd_unid_de_neg
            and Mat.cd_material=Mov.cd_material
            and Sub.cd_grupo=Mat.cd_grupo and sub.cd_sub_grupo=Mat.cd_sub_grupo
            and dpa.importar__sim_nao=1
            and Ped.cd_unid_de_neg in ('001','002','004','006','009')
            and dpa.cd_operacao_resultado_para not in ('20','21') and dpa.cd_operacao_resultado_de=mov.cd_tipo_operaca
            and Ped.cd_pedido=mov.cd_pedido
            and Ven.cd_empresa=Ped.cd_representant
            and Ped.cd_representant not in '970101'
            and PED.controle between '30' and '80'
        ) base
        GROUP BY base.cd_representante, base.nome_representante, base.categoria_consultor
        ORDER BY total_vendas DESC
        """
        try:
            results = self.execute_query(query)
            logger.info(
                f"Vendas {ano}/{mes:02d}"
                + (f" até dia {dia_corte}" if dia_corte else "")
                + f": {len(results)} representantes"
            )
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar vendas do mês {ano}/{mes:02d}: {str(e)}")
            raise

    def get_metas_representantes(self, ano: int, mes: int) -> List[Dict]:
        """
        Retorna metas dos representantes para um mês/ano da tabela PLVENITE.
        periodo no Oracle está no formato 'YYYY/MM'.
        Retorna lista de dicts com: cd_representante, meta.
        Somente leitura — não altera nenhum dado no Oracle.
        """
        periodo = f"{ano}/{mes:02d}"
        query = f"""
        SELECT
            CATEGORIA1  AS cd_representante,
            PRECO_UNIT  AS meta
        FROM PLVENITE
        WHERE TIPO_PREV_REAL = 'P'
          AND cd_plano NOT IN ('ITENS','META')
          AND periodo = '{periodo}'
        """
        try:
            results = self.execute_query(query)
            logger.info(f"Metas {periodo}: {len(results)} representantes")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar metas {periodo}: {str(e)}")
            raise

# Instância global do serviço
oracle_service = OracleService()

# Funções de conveniência para uso em outras partes do app
def test_oracle_connection():
    """Testa conexão Oracle - retorna tuple (success, message)"""
    return oracle_service.test_connection()

def get_clientes_oracle():
    """Busca clientes alvo no Oracle"""
    return oracle_service.get_clientes_alvo()

def get_pedidos_cliente_oracle(cd_cliente: str, janela_dias: int = 365, modo_especial: bool = False):
    """Busca pedidos de um cliente específico no Oracle"""
    return oracle_service.get_resumo_pedidos_cliente(cd_cliente, janela_dias=janela_dias, modo_especial=modo_especial)

def get_pedidos_cliente_periodo_oracle(cd_cliente: str, data_inicio: datetime, data_fim: datetime, modo_especial: bool = False):
    """Busca pedidos de um cliente em periodo fechado no Oracle."""
    return oracle_service.get_resumo_pedidos_cliente_periodo(
        cd_cliente,
        data_inicio=data_inicio,
        data_fim=data_fim,
        modo_especial=modo_especial,
    )


def get_itens_cliente_oracle(cd_cliente: str, janela_dias: int = 365, modo_especial: bool = False):
    """Busca itens de pedidos de um cliente específico no Oracle"""
    return oracle_service.get_itens_pedido_oracle(cd_cliente, janela_dias=janela_dias, modo_especial=modo_especial)

def get_valor_total_365dias(cd_cliente: str):
    """Busca valor total dos pedidos dos últimos 365 dias de um cliente"""
    try:
        return oracle_service.get_valor_total_365dias(cd_cliente)
    except Exception as e:
        logger.error(f"Erro ao buscar valor total 365 dias do cliente {cd_cliente}: {str(e)}")
        return 0.0

def get_quantidade_pedidos_365dias_oracle(
    codigos_clientes: Optional[List[str]] = None,
    periodo: str = "ano_atual",
):
    """Busca mapa cd_cliente -> quantidade de pedidos faturados no periodo escolhido."""
    try:
        return oracle_service.get_quantidade_pedidos_365dias(codigos_clientes or [], periodo=periodo)
    except Exception as e:
        logger.error(f"Erro ao buscar quantidade de pedidos 365 dias: {str(e)}")
        return {}

def get_meses_compra_por_cliente_oracle(
    codigos_clientes: Optional[List[str]] = None,
    periodo: str = "ano_atual",
):
    """Busca mapa cd_cliente -> lista de meses com compra no periodo escolhido."""
    global _MESES_COMPRA_CACHE

    codigos_unicos = []
    vistos = set()
    for codigo in codigos_clientes or []:
        cd = str(codigo or "").strip()
        if not cd or cd in vistos:
            continue
        vistos.add(cd)
        codigos_unicos.append(cd)
    if not codigos_unicos:
        return {}

    periodo_norm = str(periodo or "ano_atual").strip().lower()
    assinatura = hashlib.sha1(
        "\n".join(sorted(codigos_unicos)).encode("utf-8", errors="ignore")
    ).hexdigest()
    cache_key = (periodo_norm, assinatura)
    cache_item = _MESES_COMPRA_CACHE.get(cache_key)
    agora = datetime.now()
    if cache_item and (agora - cache_item["ts"]) <= _MESES_COMPRA_CACHE_TTL:
        return {key: list(value or []) for key, value in cache_item["data"].items()}

    with _MESES_COMPRA_CACHE_LOCK:
        agora = datetime.now()
        cache_item = _MESES_COMPRA_CACHE.get(cache_key)
        if cache_item and (agora - cache_item["ts"]) <= _MESES_COMPRA_CACHE_TTL:
            return {key: list(value or []) for key, value in cache_item["data"].items()}

        try:
            data = oracle_service.get_meses_compra_por_cliente(codigos_unicos, periodo=periodo_norm)
            cache_data = {key: list(value or []) for key, value in (data or {}).items()}
            _MESES_COMPRA_CACHE[cache_key] = {
                "ts": agora,
                "data": cache_data,
            }
            if len(_MESES_COMPRA_CACHE) > 32:
                itens = sorted(_MESES_COMPRA_CACHE.items(), key=lambda item: item[1]["ts"])
                _MESES_COMPRA_CACHE = dict(itens[-24:])
            return {key: list(value or []) for key, value in cache_data.items()}
        except Exception as e:
            logger.error(f"Erro ao buscar meses de compra por cliente: {str(e)}")
            return {}

def get_clientes_ativos_oracle():
    """Busca clientes ativos (0 a 180 dias com pedido) no Oracle"""
    return oracle_service.get_clientes_ativos()

def get_clientes_inativos_oracle():
    """Busca clientes inativos (181 dias a 3 anos) no Oracle"""
    return oracle_service.get_clientes_inativos()

def get_clientes_construtoras_oracle():
    """Busca clientes construtoras para televendas no Oracle"""
    return oracle_service.get_clientes_construtoras()

def get_clientes_proximos_inativacao_oracle():
    """Busca clientes proximos de inativacao (151 a 180 dias) no Oracle"""
    return oracle_service.get_clientes_proximos_inativacao()

def get_pedidos_reativacao_oracle(data_inicio: datetime, data_fim: datetime,
                                   dias_inatividade_inicio: int = 151,
                                   dias_inatividade_fim: int = 180):
    """Busca pedidos de reativação (clientes que estavam próximos da inativação e voltaram a comprar)"""
    return oracle_service.get_pedidos_reativacao(data_inicio, data_fim,
                                                   dias_inatividade_inicio,
                                                   dias_inatividade_fim)

def get_centralizadora_cliente_oracle(cd_cliente: str):
    """Busca codigo e nome da centralizadora de um cliente no Oracle"""
    return oracle_service.get_centralizadora_cliente(cd_cliente)


def get_cliente_oracle_por_cnpj(cnpj: str):
    """Busca cliente Oracle por CNPJ para pre-preenchimento de cadastro manual"""
    return oracle_service.get_cliente_por_cnpj(cnpj)


def get_cliente_oracle_por_codigo(cd_cliente: str):
    """Busca cliente Oracle por codigo para detalhes de cliente."""
    return oracle_service.get_cliente_por_codigo(cd_cliente)


def get_vinculos_supervisor_representante_oracle(codigo_supervisor: Optional[str] = None):
    """Busca vínculos supervisor-representante da TG 650 no Oracle"""
    return oracle_service.get_vinculos_supervisor_representante(codigo_supervisor)


def get_vendas_mes_representantes_oracle(ano: int, mes: int, dia_corte: Optional[int] = None) -> List[Dict]:
    """Retorna vendas faturadas por representante em um mês/ano (somente leitura)."""
    return oracle_service.get_vendas_mes_representantes(ano, mes, dia_corte=dia_corte)


def get_metas_representantes_oracle(ano: int, mes: int) -> List[Dict]:
    """Retorna metas dos representantes para um mês/ano da PLVENITE (somente leitura)."""
    return oracle_service.get_metas_representantes(ano, mes)


def get_dias_media_recebimento_oracle(codigos_clientes: Optional[List[str]] = None):
    """Busca mapa cd_empresa -> media de dias de recebimento."""
    global _DIAS_MEDIA_RECEBIMENTO_CACHE
    agora = datetime.now()
    ttl = timedelta(hours=24)
    cache_quente = (
        _DIAS_MEDIA_RECEBIMENTO_CACHE.get("ts") is not None
        and (agora - _DIAS_MEDIA_RECEBIMENTO_CACHE["ts"]) <= ttl
        and isinstance(_DIAS_MEDIA_RECEBIMENTO_CACHE.get("data"), dict)
    )

    if cache_quente:
        base = _DIAS_MEDIA_RECEBIMENTO_CACHE["data"]
    else:
        with _DIAS_MEDIA_RECEBIMENTO_CACHE_LOCK:
            agora = datetime.now()
            cache_quente = (
                _DIAS_MEDIA_RECEBIMENTO_CACHE.get("ts") is not None
                and (agora - _DIAS_MEDIA_RECEBIMENTO_CACHE["ts"]) <= ttl
                and isinstance(_DIAS_MEDIA_RECEBIMENTO_CACHE.get("data"), dict)
            )
            if cache_quente:
                base = _DIAS_MEDIA_RECEBIMENTO_CACHE["data"]
            else:
                base = {}
                try:
                    rows = oracle_service.execute_query("SELECT * FROM diasmediarecebimento ORDER BY cd_empresa")
                    def _canon_cd(v):
                        s = str(v or "").strip()
                        if not s:
                            return ""
                        dig = "".join(ch for ch in s if ch.isdigit())
                        if dig:
                            return str(int(dig))
                        return s.upper()

                    for row in rows:
                        cd = (
                            row.get("cd_empresa")
                            or row.get("cd_cliente")
                            or row.get("cdcliente")
                            or row.get("empresa")
                        )
                        if cd is None:
                            continue
                        cd_str = str(cd).strip()
                        if not cd_str:
                            continue

                        valor = None
                        for chave in (
                            "media_dias",
                            "dias_media_recebimento",
                            "diasmediarecebimento",
                            "dias_media",
                            "media_recebimento",
                            "prazo_medio",
                            "media",
                            "dias",
                        ):
                            if chave in row and row.get(chave) is not None:
                                valor = row.get(chave)
                                break
                        if valor is None:
                            for k, v in row.items():
                                if v is None:
                                    continue
                                lk = str(k).lower()
                                if "cd_" in lk or "empresa" in lk or "cliente" in lk or "codigo" in lk:
                                    continue
                                try:
                                    valor = float(v)
                                    break
                                except Exception:
                                    continue
                        if valor is None:
                            continue
                        try:
                            base[cd_str] = float(valor)
                            cd_canon = _canon_cd(cd_str)
                            if cd_canon and cd_canon not in base:
                                base[cd_canon] = float(valor)
                        except Exception:
                            continue
                except Exception as e:
                    logger.warning(f"Falha ao buscar diasmediarecebimento: {e}")

                _DIAS_MEDIA_RECEBIMENTO_CACHE = {"ts": agora, "data": base}

    if not codigos_clientes:
        return dict(base)
    def _canon_cd(v):
        s = str(v or "").strip()
        if not s:
            return ""
        dig = "".join(ch for ch in s if ch.isdigit())
        if dig:
            return str(int(dig))
        return s.upper()

    resultado = {}
    for c in codigos_clientes:
        cd = str(c or "").strip()
        if not cd:
            continue
        if cd in base:
            resultado[cd] = base[cd]
            continue
        cd_canon = _canon_cd(cd)
        if cd_canon and cd_canon in base:
            resultado[cd] = base[cd_canon]
    return resultado


def get_pedidos_em_andamento_recentes_oracle(
    codigos_clientes: Optional[List[str]] = None,
    dias_recencia: int = 45,
):
    """Busca mapa cd_cliente -> pedido recente em andamento."""
    global _PEDIDOS_ANDAMENTO_CACHE

    codigos_unicos = []
    vistos = set()
    for codigo in codigos_clientes or []:
        cd = str(codigo or "").strip()
        if not cd or cd in vistos:
            continue
        vistos.add(cd)
        codigos_unicos.append(cd)
    if not codigos_unicos:
        return {}

    dias_recencia_norm = max(1, min(int(dias_recencia or 45), 120))
    assinatura = hashlib.sha1(
        "\n".join(sorted(codigos_unicos)).encode("utf-8", errors="ignore")
    ).hexdigest()
    cache_key = (dias_recencia_norm, assinatura)
    cache_item = _PEDIDOS_ANDAMENTO_CACHE.get(cache_key)
    agora = datetime.now()
    if cache_item and (agora - cache_item["ts"]) <= _PEDIDOS_ANDAMENTO_CACHE_TTL:
        return {key: dict(value or {}) for key, value in cache_item["data"].items()}

    data = oracle_service.get_pedidos_em_andamento_recentes(
        codigos_unicos,
        dias_recencia=dias_recencia_norm,
    )
    _PEDIDOS_ANDAMENTO_CACHE[cache_key] = {
        "ts": agora,
        "data": {key: dict(value or {}) for key, value in (data or {}).items()},
    }
    if len(_PEDIDOS_ANDAMENTO_CACHE) > 32:
        itens = sorted(_PEDIDOS_ANDAMENTO_CACHE.items(), key=lambda item: item[1]["ts"])
        _PEDIDOS_ANDAMENTO_CACHE = dict(itens[-24:])
    return data

