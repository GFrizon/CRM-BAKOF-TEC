import logging


logger = logging.getLogger(__name__)


def invalidar_caches_listagens_clientes(motivo: str = ""):
    """
    Invalida caches em memoria relacionados as listagens de clientes.
    Nao altera regra de negocio; apenas garante consistencia apos escritas.
    """
    try:
        from routes.clientes_ligacoes.html_response_cache import (
            limpar_cache_html_listagens,
        )

        limpar_cache_html_listagens()
    except Exception as e:
        logger.warning("Falha ao limpar cache HTML das listagens: %s", e)

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

    try:
        from routes.clientes_ligacoes.listagem_operacional import limpar_cache_operacional

        limpar_cache_operacional()
    except Exception as e:
        logger.warning("Falha ao limpar cache operacional: %s", e)

    try:
        from routes.clientes_ligacoes.ativos_tab import limpar_cache_clientes_ativos
        from routes.clientes_ligacoes.listagem_ativos import limpar_cache_locais_ativos

        limpar_cache_clientes_ativos()
        limpar_cache_locais_ativos()
    except Exception as e:
        logger.warning("Falha ao limpar cache de clientes ativos: %s", e)

    motivo_norm = (motivo or "").strip().lower()
    escrita_apenas_local = (
        "registro de ligacao" in motivo_norm
        or "garantia de cliente local oracle" in motivo_norm
    )
    if not escrita_apenas_local:
        try:
            from routes.clientes_ligacoes.construtoras_tab import limpar_cache_clientes_construtoras

            limpar_cache_clientes_construtoras()
        except Exception as e:
            logger.warning("Falha ao limpar cache de clientes construtoras: %s", e)

        try:
            from services.representante_metricas_cache_service import (
                invalidar_metricas_representante_diarias,
            )
            from services.representante_projection_cache_service import (
                invalidar_projecoes_representante_diarias,
            )

            invalidar_metricas_representante_diarias()
            invalidar_projecoes_representante_diarias()
        except Exception as e:
            logger.warning("Falha ao limpar caches diarios de representantes: %s", e)

    try:
        from routes.clientes_ligacoes.badges import limpar_cache_badges_representante

        limpar_cache_badges_representante()
    except Exception as e:
        logger.warning("Falha ao limpar cache de badges por representante: %s", e)

    if motivo:
        logger.info("Caches de listagens invalidados. Motivo: %s", motivo)
