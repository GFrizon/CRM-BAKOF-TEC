from datetime import datetime, timedelta

from sqlalchemy.orm import load_only

from core.models import Cliente
from services.inativos_snapshot_service import (
    carregar_snapshot_inativos_oracle,
    montar_mapa_snapshot_inativos,
    salvar_snapshot_inativos_oracle,
    snapshot_inativos_cobre_janela,
)

_INATIVOS_ORACLE_ENRICH_CACHE = {
    "ts": None,
    "data": None,
}
_INATIVOS_ORACLE_ENRICH_TTL = timedelta(hours=24)

_INATIVOS_RESULTADO_CACHE = {
    "ts": None,
    "data": None,
}
_INATIVOS_RESULTADO_TTL = timedelta(minutes=2)


def limpar_cache_inativos_enriquecidos():
    _INATIVOS_ORACLE_ENRICH_CACHE["ts"] = None
    _INATIVOS_ORACLE_ENRICH_CACHE["data"] = None
    _INATIVOS_RESULTADO_CACHE["ts"] = None
    _INATIVOS_RESULTADO_CACHE["data"] = None


def carregar_clientes_inativos_enriquecidos(logger):
    agora = datetime.now()
    if (
        _INATIVOS_RESULTADO_CACHE["ts"] is not None
        and (agora - _INATIVOS_RESULTADO_CACHE["ts"]) <= _INATIVOS_RESULTADO_TTL
        and isinstance(_INATIVOS_RESULTADO_CACHE["data"], list)
    ):
        return _INATIVOS_RESULTADO_CACHE["data"]
    limite_max = datetime.now() - timedelta(days=181)
    limite_min = datetime.now() - timedelta(days=1095)

    clientes_inativos_local = (
        Cliente.query
        .options(
            load_only(
                Cliente.id,
                Cliente.nome,
                Cliente.cnpj,
                Cliente.telefone,
                Cliente.telefone2,
                Cliente.ativo,
                Cliente.cd_cliente_oracle,
                Cliente.categoria_consultor,
                Cliente.conceito,
                Cliente.ultimo_pedido_oracle,
                Cliente.valor_ultimo_pedido,
                Cliente.situacao_ultimo_pedido,
                Cliente.representante_oracle,
                Cliente.municipio,
                Cliente.uf,
                Cliente.contato,
                Cliente.data_ultima_sincronizacao,
            )
        )
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
    codigos_inativos_oracle = set()
    oracle_enriquecimento_ok = False
    snapshot = carregar_snapshot_inativos_oracle()
    if snapshot and snapshot_inativos_cobre_janela(snapshot):
        codigos_inativos_oracle, centralizadora_por_cd = montar_mapa_snapshot_inativos(snapshot)
        centralizadora_por_cd["__codigos_inativos_oracle__"] = list(codigos_inativos_oracle)
        _INATIVOS_ORACLE_ENRICH_CACHE["ts"] = datetime.now()
        _INATIVOS_ORACLE_ENRICH_CACHE["data"] = dict(centralizadora_por_cd)
        oracle_enriquecimento_ok = bool(codigos_inativos_oracle)
    elif snapshot:
        logger.info("Snapshot de inativos fora da janela atual (181-1095). Forcando refresh.")
    elif cache_quente:
        centralizadora_por_cd = dict(_INATIVOS_ORACLE_ENRICH_CACHE["data"])
        codigos_cache = centralizadora_por_cd.get("__codigos_inativos_oracle__") or []
        codigos_inativos_oracle = {
            str(cd or "").strip()
            for cd in codigos_cache
            if str(cd or "").strip()
        }
        oracle_enriquecimento_ok = bool(codigos_inativos_oracle)
    else:
        try:
            from oracle_service import get_clientes_inativos_oracle as _get_clientes_inativos_oracle

            inativos_oracle_raw = _get_clientes_inativos_oracle() or []
            for row in inativos_oracle_raw:
                cd = str(row.get("cd_cliente") or "").strip()
                if not cd or cd in centralizadora_por_cd:
                    continue
                codigos_inativos_oracle.add(cd)
                centralizadora_por_cd[cd] = {
                    "cd_centralizado": row.get("cd_centralizado"),
                    "nome_centralizadora": row.get("nome_centralizadora"),
                }
            # Em cache também guardamos a lista de códigos inativos Oracle para
            # evitar exibir cliente que já saiu da carteira de inativos.
            centralizadora_por_cd["__codigos_inativos_oracle__"] = list(codigos_inativos_oracle)
            salvar_snapshot_inativos_oracle(inativos_oracle_raw)
            _INATIVOS_ORACLE_ENRICH_CACHE["ts"] = datetime.now()
            _INATIVOS_ORACLE_ENRICH_CACHE["data"] = dict(centralizadora_por_cd)
            oracle_enriquecimento_ok = True
        except Exception as e:
            logger.warning(f"Falha ao enriquecer centralizadoras dos inativos via Oracle: {e}")

    # Compatibilidade com cache antigo (sem lista de codigos) ou cache inconsistente:
    # busca Oracle na hora para garantir classificação correta de inativos.
    if not codigos_inativos_oracle:
        try:
            from oracle_service import get_clientes_inativos_oracle as _get_clientes_inativos_oracle

            inativos_oracle_raw = _get_clientes_inativos_oracle() or []
            for row in inativos_oracle_raw:
                cd = str(row.get("cd_cliente") or "").strip()
                if not cd:
                    continue
                codigos_inativos_oracle.add(cd)
                if cd not in centralizadora_por_cd:
                    centralizadora_por_cd[cd] = {
                        "cd_centralizado": row.get("cd_centralizado"),
                        "nome_centralizadora": row.get("nome_centralizadora"),
                    }
            if codigos_inativos_oracle:
                centralizadora_por_cd["__codigos_inativos_oracle__"] = list(codigos_inativos_oracle)
                salvar_snapshot_inativos_oracle(inativos_oracle_raw)
                _INATIVOS_ORACLE_ENRICH_CACHE["ts"] = datetime.now()
                _INATIVOS_ORACLE_ENRICH_CACHE["data"] = dict(centralizadora_por_cd)
                oracle_enriquecimento_ok = True
        except Exception as e:
            logger.warning(f"Falha ao validar codigos de inativos no Oracle: {e}")

    # Se Oracle respondeu, ele é a referência para decidir quem está inativo hoje.
    if codigos_inativos_oracle:
        clientes_inativos_local = [
            c for c in clientes_inativos_local
            if str(c.cd_cliente_oracle or "").strip() in codigos_inativos_oracle
        ]

    # Deduplicação por código Oracle (evita cliente duplicado na listagem).
    # Critério: maior data_ultima_sincronizacao, depois maior id.
    dedup_por_cd = {}
    for c in clientes_inativos_local:
        cd = str(c.cd_cliente_oracle or "").strip()
        if not cd:
            continue
        atual = dedup_por_cd.get(cd)
        if not atual:
            dedup_por_cd[cd] = c
            continue
        atual_pedido = atual.ultimo_pedido_oracle or datetime.min
        novo_pedido = c.ultimo_pedido_oracle or datetime.min
        atual_sync = atual.data_ultima_sincronizacao or datetime.min
        novo_sync = c.data_ultima_sincronizacao or datetime.min
        if (novo_pedido, novo_sync, int(c.id or 0)) > (atual_pedido, atual_sync, int(atual.id or 0)):
            dedup_por_cd[cd] = c
    clientes_inativos_local = list(dedup_por_cd.values())

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
    origem_ref = "oracle+local" if (oracle_enriquecimento_ok or codigos_inativos_oracle) else "local"
    logger.info(
        "Buscados %s clientes inativos (%s, dedup por cd) snapshot_data_ref=%s atualizado_em=%s",
        len(clientes_oracle_inativos),
        origem_ref,
        (snapshot or {}).get("data_ref"),
        (snapshot or {}).get("atualizado_em"),
    )
    _INATIVOS_RESULTADO_CACHE["ts"] = datetime.now()
    _INATIVOS_RESULTADO_CACHE["data"] = clientes_oracle_inativos
    return clientes_oracle_inativos
