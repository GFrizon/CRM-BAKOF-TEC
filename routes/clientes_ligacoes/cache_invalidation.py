import logging


logger = logging.getLogger(__name__)


def invalidar_caches_listagens_clientes(motivo: str = ""):
    """
    Invalida caches em memoria relacionados as listagens de clientes.
    Nao altera regra de negocio; apenas garante consistencia apos escritas.
    """
    try:
        from routes.clientes_ligacoes.oracle_tab import limpar_cache_clientes_oracle

        limpar_cache_clientes_oracle()
    except Exception as e:
        logger.warning("Falha ao limpar cache Oracle 90-150: %s", e)

    try:
        from routes.clientes_ligacoes.inativos_tab import limpar_cache_inativos_enriquecidos

        limpar_cache_inativos_enriquecidos()
    except Exception as e:
        logger.warning("Falha ao limpar cache de inativos enriquecidos: %s", e)

    try:
        from routes.clientes_ligacoes.listagem_routes import limpar_cache_contagem_inativos

        limpar_cache_contagem_inativos()
    except Exception as e:
        logger.warning("Falha ao limpar cache de contagem de inativos: %s", e)

    if motivo:
        logger.info("Caches de listagens invalidados. Motivo: %s", motivo)
