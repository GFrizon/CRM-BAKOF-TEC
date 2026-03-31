"""
Serviço de conexão e integração com banco Oracle
Para busca de dados estratégicos de clientes (CRM híbrido)
"""

import os
import logging
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dotenv import load_dotenv
from database_utils import retry_oracle_connection, retry_database

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
                    self._pool = oracledb.create_pool(
                        user=self.config['user'],
                        password=self.config['password'],
                        dsn=self.config['dsn'],
                        min=2,
                        max=10,
                        increment=1,
                        getmode=oracledb.POOL_GETMODE_WAIT,
                        ping_interval=60,
                        ping_timeout=5000,
                    )
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
        # REGRA VALIDADA (2026-03): 90-120 dias, 1 linha por cliente (ultimo pedido valido).
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
              PED.cd_unid_de_neg,
              row_number() over (
                partition by PED.cd_cliente
                order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
              ) as rn
            from fapedido PED
            join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
            where DPA.cd_operacao_resultado_para not in ('20','21')
              and PED.controle not in ('85','96','99','86')
              and upper(trim(nvl(to_char(PED.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')
        ),
        clientes_alvo as (
            select *
            from pedidos_validos
            where rn = 1
              and dt_pedido between (sysdate - 120) and (sysdate - 90)
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
          and CLI.tipo_de_empresa = 'R'
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
        Busca clientes inativos (181 dias a 2 anos sem pedidos) para televendas
        
        Returns:
            Lista de clientes com dados estratégicos
        """
        # REGRA VALIDADA (2026-03): inativos 181-730 dias, 1 linha por cliente (ultimo pedido valido).
        query = """
        with pedidos_validos as (
            select
              PED.cd_cliente,
              PED.dt_pedido,
              PED.cd_pedido,
              PED.total_pedido,
              PED.situacao,
              PED.desc_cond_pagto,
              PED.cd_unid_de_neg,
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
        ),
        clientes_alvo as (
            select *
            from pedidos_validos
            where rn = 1
              and dt_pedido between (sysdate - 730) and (sysdate - 181)
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
          and CLI.tipo_de_empresa = 'R'
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
            logger.info(f"Buscados {len(results)} clientes inativos do Oracle")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar clientes inativos: {str(e)}")
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
              PED.cd_unid_de_neg,
              row_number() over (
                partition by PED.cd_cliente
                order by PED.dt_pedido desc, PED.cd_pedido desc nulls last
              ) as rn
            from fapedido PED
            join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
            where DPA.cd_operacao_resultado_para not in ('20','21')
              and PED.controle not in ('85','96','99','86')
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
          and CLI.tipo_de_empresa = 'R'
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
        if dias > 730:
            dias = 730

        filtro_faturamento = (
            "and nvl(gerou_faturamen, 0) = 1 "
            "and upper(trim(nvl(to_char(situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')"
        )

        query = f"""
        select 
            dt_pedido,
            total_pedido,
            situacao,
            desc_cond_pagto,
            cd_unid_de_neg
        from fapedido
        where cd_cliente = :cd_cliente
          and dt_pedido >= (sysdate - :janela_dias)
          {filtro_faturamento}
        order by dt_pedido desc
        """
        
        try:
            results = self.execute_query(query, {'cd_cliente': cd_cliente, 'janela_dias': dias})
            logger.info(f"Buscados {len(results)} pedidos para cliente {cd_cliente}")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar pedidos do cliente {cd_cliente}: {str(e)}")
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
        if dias > 730:
            dias = 730

        filtro_faturamento = (
            "AND nvl(p.gerou_faturamen, 0) = 1 "
            "AND upper(trim(nvl(to_char(p.situacao), ''))) not in ('C', 'CANCELADO', 'D', 'DEVOLVIDO')"
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
            COALESCE(m.descricao, i.cd_material) as nome_produto
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
        """
        cnpj_digits = "".join(ch for ch in str(cnpj or "") if ch.isdigit())
        if len(cnpj_digits) < 11:
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
          and CLI.tipo_de_empresa = 'R'
        order by UPE.dt_pedido desc nulls last, CLI.cd_empresa
        fetch first 1 rows only
        """

        try:
            results = self.execute_query(query, {"cnpj": cnpj_digits})
            if not results:
                return None
            return results[0]
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
          and CLI.tipo_de_empresa = 'R'
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

def get_clientes_inativos_oracle():
    """Busca clientes inativos (181 dias a 2 anos) no Oracle"""
    return oracle_service.get_clientes_inativos()


def get_clientes_proximos_inativacao_oracle():
    """Busca clientes proximos de inativacao (151 a 180 dias) no Oracle"""
    return oracle_service.get_clientes_proximos_inativacao()


def get_centralizadora_cliente_oracle(cd_cliente: str):
    """Busca codigo e nome da centralizadora de um cliente no Oracle"""
    return oracle_service.get_centralizadora_cliente(cd_cliente)


def get_cliente_oracle_por_cnpj(cnpj: str):
    """Busca cliente Oracle por CNPJ para pre-preenchimento de cadastro manual"""
    return oracle_service.get_cliente_por_cnpj(cnpj)


def get_cliente_oracle_por_codigo(cd_cliente: str):
    """Busca cliente Oracle por codigo para detalhes de cliente."""
    return oracle_service.get_cliente_por_codigo(cd_cliente)
