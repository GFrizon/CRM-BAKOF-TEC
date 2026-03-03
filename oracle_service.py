"""
Serviço de conexão e integração com banco Oracle
Para busca de dados estratégicos de clientes (CRM híbrido)
"""

import os
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from database_utils import retry_oracle_connection, retry_database

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        self.connection = None
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
                oracledb.init_oracle_client()
                logger.info("Oracle Client inicializado (modo thick)")
            except Exception:
                logger.info("Usando modo thin client (sem Oracle Client)")
    
    def test_connection(self) -> Tuple[bool, str]:
        """Testa conexão com o banco Oracle"""
        if not ORACLE_LIB:
            return False, "Nenhuma biblioteca Oracle instalada. Instale oracledb ou cx_Oracle"
        
        try:
            if ORACLE_LIB == 'oracledb':
                conn = oracledb.connect(**self.config)
            else:  # cx_Oracle
                conn = cx_Oracle.connect(**self.config)
            
            version = conn.version
            conn.close()
            return True, f"Conexão bem sucedida! Oracle version: {version} (usando {ORACLE_LIB})"
        except oracledb.DatabaseError as e:
            logger.error(f"Erro de banco Oracle: {str(e)}")
            return False, f"Erro de banco de dados: {str(e)}"
        except oracledb.InterfaceError as e:
            logger.error(f"Erro de interface Oracle: {str(e)}")
            return False, f"Erro de conexão/interface: {str(e)}"
        except Exception as e:
            logger.error(f"Erro inesperado ao conectar Oracle: {str(e)}")
            return False, f"Erro de conexão: {str(e)}"
    
    @retry_oracle_connection(max_attempts=3, delay=2.0)
    def get_connection(self):
        """Obtém conexão com o banco Oracle (singleton) com retry"""
        if not ORACLE_LIB:
            raise ImportError("Nenhuma biblioteca Oracle instalada")
            
        if self.connection is None:
            try:
                if ORACLE_LIB == 'oracledb':
                    self.connection = oracledb.connect(**self.config)
                else:  # cx_Oracle
                    self.connection = cx_Oracle.connect(**self.config)
                logger.info(f"Conexão Oracle estabelecida com sucesso (usando {ORACLE_LIB})")
            except oracledb.DatabaseError as e:
                logger.error(f"Erro de banco Oracle ao estabelecer conexão: {str(e)}")
                raise ConnectionError(f"Erro de banco de dados Oracle: {str(e)}")
            except oracledb.InterfaceError as e:
                logger.error(f"Erro de interface Oracle ao estabelecer conexão: {str(e)}")
                raise ConnectionError(f"Erro de interface/conexão Oracle: {str(e)}")
            except Exception as e:
                logger.error(f"Erro inesperado ao estabelecer conexão Oracle: {str(e)}")
                raise
        return self.connection
    
    def close_connection(self):
        """Fecha conexão com Oracle"""
        if self.connection:
            try:
                self.connection.close()
                self.connection = None
                logger.info("Conexão Oracle fechada")
            except Exception as e:
                logger.error(f"Erro ao fechar conexão Oracle: {str(e)}")
    
    @retry_database(max_attempts=3, delay=1.0)
    def execute_query(self, query: str, params: Optional[Dict] = None) -> List[Dict]:
        """
        Executa query SQL e retorna resultados como lista de dicionários
        
        Args:
            query: Query SQL a ser executada
            params: Parâmetros opcionais para a query
            
        Returns:
            Lista de dicionários com os resultados
        """
        try:
            conn = self.get_connection()
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
            
            cursor.close()
            logger.info(f"Query executada com sucesso: {len(results)} registros")
            return results
            
        except Exception as e:
            logger.error(f"Erro ao executar query: {str(e)}")
            raise
    
    def get_clientes_alvo(self) -> List[Dict]:
        """
        Busca clientes alvo usando a query fornecida
        
        Returns:
            Lista de clientes com dados estratégicos
        """
        query = """
        with clientes_alvo as (
            select cd_cliente
            from fapedido
            group by cd_cliente
            having max(dt_pedido) between (sysdate - 120) and (sysdate - 90)
        )
        select 
          PED.dt_pedido,
          PED.cd_cliente,
          CLI.nome_completo as cliente,
          CLI.cnpj_cpf as cnpj,  -- CNPJ/CPF do cliente
          CLI.fax_fone as telefone,  -- Telefone principal
          REP.nome_completo || ' - ' || CLI.cd_representant as representante,
          PED.total_pedido,
          PED.situacao,
          PED.desc_cond_pagto,
          PED.cd_unid_de_neg,
          coalesce(TGS.categoria,'999') || ' - ' || TG1.desc_categoria as consultor,
          case 
             when CLI.conceito = 'L' then 'LIBERADO'
             when CLI.conceito = 'B' then 'INADIMPLENTE'
             when trim(CLI.conceito) is null then 'SEM CONCEITO'
             else 'SEM CONCEITO'
          end as conceito
        from fapedido PED
        join clientes_alvo CA on CA.cd_cliente = PED.cd_cliente
        join geempres CLI on CLI.cd_empresa = PED.cd_cliente
        left join Geelemen TGS on TGS.cd_tg = 634 and TGS.elemento = CLI.cd_representant
        left join Gecatego TG1 on TG1.cd_tg = 634 and TG1.categoria = coalesce(TGS.categoria,'999')
        left join geempres REP on REP.cd_empresa = CLI.cd_representant
        join dexpara DPA on DPA.cd_operacao_resultado_de = PED.cd_tipo_operaca
        where PED.dt_pedido between (sysdate - 120) and (sysdate - 90)
          and CLI.pessoa = '0'
          and CLI.tipo_de_empresa = 'R'
          and DPA.cd_operacao_resultado_para not in ('20','21')
          and PED.controle not in ('85','96','99','86')
          and case 
                when regexp_like(TGS.categoria, '^[0-9]+$') 
                     and to_number(TGS.categoria) <= 100
                then 1
                else 0
              end = 1
        order by 1 asc
        """
        
        try:
            results = self.execute_query(query)
            logger.info(f"Buscados {len(results)} clientes alvo do Oracle")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar clientes alvo: {str(e)}")
            raise
    
    def get_resumo_pedidos_cliente(self, cd_cliente: str) -> List[Dict]:
        """
        Busca resumo de pedidos de um cliente específico
        
        Args:
            cd_cliente: Código do cliente no Oracle
            
        Returns:
            Lista com histórico de pedidos
        """
        query = """
        select 
            dt_pedido,
            total_pedido,
            situacao,
            desc_cond_pagto,
            cd_unid_de_neg
        from fapedido
        where cd_cliente = :cd_cliente
          and dt_pedido between (sysdate - 365) and sysdate
        order by dt_pedido desc
        """
        
        try:
            results = self.execute_query(query, {'cd_cliente': cd_cliente})
            logger.info(f"Buscados {len(results)} pedidos para cliente {cd_cliente}")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar pedidos do cliente {cd_cliente}: {str(e)}")
            raise
    
    def get_itens_pedido_oracle(self, cd_cliente: str) -> List[Dict]:
        """
        Busca itens dos pedidos de um cliente específico com nomes dos produtos
        
        Args:
            cd_cliente: Código do cliente no Oracle
            
        Returns:
            Lista com itens dos pedidos incluindo nomes dos produtos
        """
        query = """
        SELECT 
            i.cd_material as idproduto,
            i.quantidade,
            i.pr_unitario as precounitario,
            i.vl_total_item_l as valorliquidoitem,
            i.sequencia as ordenacao,
            i.dt_item,
            p.dt_pedido,
            p.cd_pedido,
            COALESCE(m.descricao, i.cd_material) as nome_produto
        FROM fapedido p
        JOIN FAITEMPE i ON i.cd_pedido = p.cd_pedido
        LEFT JOIN esmateri m ON m.cd_material = i.cd_material
        WHERE p.cd_cliente = :cd_cliente
          AND p.dt_pedido between (sysdate - 365) and sysdate
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
            results = self.execute_query(query, {'cd_cliente': cd_cliente})
            logger.info(f"Buscados {len(results)} itens para cliente {cd_cliente}")
            return results
        except Exception as e:
            logger.error(f"Erro ao buscar itens do cliente {cd_cliente}: {str(e)}")
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

def get_pedidos_cliente_oracle(cd_cliente: str):
    """Busca pedidos de um cliente específico no Oracle"""
    return oracle_service.get_resumo_pedidos_cliente(cd_cliente)

def get_itens_cliente_oracle(cd_cliente: str):
    """Busca itens de pedidos de um cliente específico no Oracle"""
    return oracle_service.get_itens_pedido_oracle(cd_cliente)
