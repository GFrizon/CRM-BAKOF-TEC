"""
Utilitários para retry pattern em conexões de banco
"""

import time
import logging
from functools import wraps
from typing import Callable, Type, Tuple, Union, Any
from sqlalchemy.exc import OperationalError, IntegrityError, DatabaseError

logger = logging.getLogger(__name__)

def retry_database(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (
        OperationalError,
        DatabaseError,
        ConnectionError,
        TimeoutError
    )
):
    """
    Decorator para retry pattern em operações de banco de dados
    
    Args:
        max_attempts: Número máximo de tentativas
        delay: Tempo inicial de espera em segundos
        backoff: Fator multiplicativo de espera
        exceptions: Tupla de exceções que triggers retry
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            current_delay = delay
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts - 1:
                        logger.error(f"Erro persistente após {max_attempts} tentativas em {func.__name__}: {str(e)}")
                        raise
                    
                    logger.warning(f"Tentativa {attempt + 1}/{max_attempts} falhou em {func.__name__}: {str(e)}. Retrying em {current_delay}s...")
                    time.sleep(current_delay)
                    current_delay *= backoff
                    
                except IntegrityError as e:
                    # Erros de integridade não devem retry
                    logger.error(f"Erro de integridade em {func.__name__}: {str(e)}")
                    raise
                    
                except Exception as e:
                    # Outras exceções não devem retry
                    logger.error(f"Exceção inesperada em {func.__name__}: {str(e)}")
                    raise
            
            # Nunca deveria chegar aqui
            if last_exception:
                raise last_exception
                
        return wrapper
    return decorator


def retry_oracle_connection(
    max_attempts: int = 3,
    delay: float = 2.0,
    backoff: float = 1.5
):
    """
    Decorator específico para conexões Oracle
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            current_delay = delay
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # Verificar se é erro de conexão Oracle
                    error_msg = str(e).lower()
                    is_connection_error = any(
                        keyword in error_msg 
                        for keyword in [
                            'connection', 'timeout', 'network', 
                            'unavailable', 'refused', 'closed'
                        ]
                    )
                    
                    if not is_connection_error or attempt == max_attempts - 1:
                        logger.error(f"Erro Oracle em {func.__name__}: {str(e)}")
                        raise
                    
                    logger.warning(f"Tentativa {attempt + 1}/{max_attempts} falhou na conexão Oracle. Retrying em {current_delay}s...")
                    time.sleep(current_delay)
                    current_delay *= backoff
            
            if last_exception:
                raise last_exception
                
        return wrapper
    return decorator


class DatabaseConnectionPool:
    """
    Pool de conexões com retry automático
    """
    
    def __init__(self, connection_func: Callable, max_size: int = 5):
        self.connection_func = connection_func
        self.max_size = max_size
        self._pool = []
        self._active_connections = 0
    
    @retry_database(max_attempts=3, delay=1.0)
    def get_connection(self):
        """
        Obtém conexão do pool com retry
        """
        if self._pool:
            conn = self._pool.pop()
            self._active_connections += 1
            return conn
        
        if self._active_connections < self.max_size:
            conn = self.connection_func()
            self._active_connections += 1
            return conn
        
        raise Exception("Pool de conexões esgotado")
    
    def release_connection(self, conn):
        """
        Libera conexão de volta ao pool
        """
        try:
            # Testar se conexão ainda está válida
            conn.execute("SELECT 1")
            self._pool.append(conn)
            self._active_connections -= 1
        except Exception:
            # Conexão inválida, fechar e decrementar
            try:
                conn.close()
            except:
                pass
            self._active_connections -= 1
