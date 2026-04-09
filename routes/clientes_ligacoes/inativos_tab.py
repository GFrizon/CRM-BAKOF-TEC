from datetime import datetime, timedelta

from core.models import Cliente

_INATIVOS_ORACLE_ENRICH_CACHE = {
    "ts": None,
    "data": None,
}
_INATIVOS_ORACLE_ENRICH_TTL = timedelta(minutes=10)


def carregar_clientes_inativos_enriquecidos(logger):
    limite_max = datetime.now() - timedelta(days=181)
    limite_min = datetime.now() - timedelta(days=730)

    clientes_inativos_local = (
        Cliente.query
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.between(limite_min, limite_max),
        )
        .all()
    )

    # Enriquecer centralizadora via Oracle para exibir na listagem de inativos.
    centralizadora_por_cd = {}
    cache_quente = (
        _INATIVOS_ORACLE_ENRICH_CACHE["ts"] is not None
        and (datetime.now() - _INATIVOS_ORACLE_ENRICH_CACHE["ts"]) <= _INATIVOS_ORACLE_ENRICH_TTL
        and isinstance(_INATIVOS_ORACLE_ENRICH_CACHE["data"], dict)
    )
    if cache_quente:
        centralizadora_por_cd = dict(_INATIVOS_ORACLE_ENRICH_CACHE["data"])
    else:
        try:
            from oracle_service import get_clientes_inativos_oracle as _get_clientes_inativos_oracle

            inativos_oracle_raw = _get_clientes_inativos_oracle() or []
            for row in inativos_oracle_raw:
                cd = str(row.get("cd_cliente") or "").strip()
                if not cd or cd in centralizadora_por_cd:
                    continue
                centralizadora_por_cd[cd] = {
                    "cd_centralizado": row.get("cd_centralizado"),
                    "nome_centralizadora": row.get("nome_centralizadora"),
                }
            _INATIVOS_ORACLE_ENRICH_CACHE["ts"] = datetime.now()
            _INATIVOS_ORACLE_ENRICH_CACHE["data"] = dict(centralizadora_por_cd)
        except Exception as e:
            logger.warning(f"Falha ao enriquecer centralizadoras dos inativos via Oracle: {e}")

    clientes_oracle_inativos = [
        {
            "cd_cliente": c.cd_cliente_oracle,
            "cliente": c.nome,
            "cnpj": c.cnpj,
            "telefone1": c.telefone,
            "telefone2": c.telefone2,
            "representante": c.representante_oracle,
            "consultor": c.categoria_consultor,
            "conceito": c.conceito,
            "municipio": c.municipio,
            "uf": c.uf,
            "contato": c.contato,
            "dt_pedido": c.ultimo_pedido_oracle,
            "total_pedido": c.valor_ultimo_pedido,
            "situacao": c.situacao_ultimo_pedido,
            "cd_centralizado": (
                centralizadora_por_cd.get(str(c.cd_cliente_oracle).strip(), {}).get("cd_centralizado")
                if c.cd_cliente_oracle else None
            ),
            "nome_centralizadora": (
                centralizadora_por_cd.get(str(c.cd_cliente_oracle).strip(), {}).get("nome_centralizadora")
                if c.cd_cliente_oracle else None
            ),
        }
        for c in clientes_inativos_local
    ]
    logger.info(f"Buscados {len(clientes_oracle_inativos)} clientes inativos da base local sincronizada")
    return clientes_oracle_inativos
