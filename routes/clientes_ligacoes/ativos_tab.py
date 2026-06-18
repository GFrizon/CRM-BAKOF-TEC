from datetime import datetime, timedelta

from sqlalchemy.orm import load_only

from core.models import Cliente
from services.ativos_snapshot_service import (
    carregar_snapshot_ativos_oracle,
    montar_mapa_snapshot_ativos,
    rows_snapshot_ativos,
    salvar_snapshot_ativos_oracle,
)


_ATIVOS_ORACLE_ENRICH_CACHE = {
    "ts": None,
    "data": None,
}
_ATIVOS_ORACLE_ENRICH_TTL = timedelta(hours=24)
_ATIVOS_ORACLE_RAW_CACHE = {
    "ts": None,
    "data": None,
}


def limpar_cache_clientes_ativos():
    _ATIVOS_ORACLE_ENRICH_CACHE["ts"] = None
    _ATIVOS_ORACLE_ENRICH_CACHE["data"] = None
    _ATIVOS_ORACLE_RAW_CACHE["ts"] = None
    _ATIVOS_ORACLE_RAW_CACHE["data"] = None


def carregar_clientes_ativos_oracle_deduplicados(logger):
    cache_quente = (
        _ATIVOS_ORACLE_RAW_CACHE["ts"] is not None
        and (datetime.now() - _ATIVOS_ORACLE_RAW_CACHE["ts"]) <= _ATIVOS_ORACLE_ENRICH_TTL
        and isinstance(_ATIVOS_ORACLE_RAW_CACHE["data"], list)
    )
    if cache_quente:
        return list(_ATIVOS_ORACLE_RAW_CACHE["data"] or [])

    snapshot = carregar_snapshot_ativos_oracle()
    snapshot_rows = rows_snapshot_ativos(snapshot) if snapshot else []
    if snapshot_rows:
        ativos_oracle_raw = snapshot_rows
        logger.info(
            "Carregados %s clientes ativos do snapshot diario data_ref=%s atualizado_em=%s.",
            len(snapshot_rows),
            snapshot.get("data_ref"),
            snapshot.get("atualizado_em"),
        )
    else:
        try:
            from oracle_service import get_clientes_ativos_oracle as _get_clientes_ativos_oracle

            ativos_oracle_raw = _get_clientes_ativos_oracle() or []
        except Exception as e:
            logger.warning(f"Falha ao buscar clientes ativos via Oracle: {e}")
            ativos_oracle_raw = []

    por_cd = {}
    for row in ativos_oracle_raw:
        cd = str((row or {}).get("cd_cliente") or "").strip()
        if not cd:
            continue
        atual = por_cd.get(cd)
        if not atual:
            por_cd[cd] = row
            continue
        dt_novo = row.get("dt_pedido")
        dt_atual = atual.get("dt_pedido")
        if dt_novo and (not dt_atual or dt_novo > dt_atual):
            por_cd[cd] = row

    resultado = list(por_cd.values())
    if ativos_oracle_raw and not snapshot_rows:
        salvar_snapshot_ativos_oracle(ativos_oracle_raw)
    _ATIVOS_ORACLE_RAW_CACHE["ts"] = datetime.now()
    _ATIVOS_ORACLE_RAW_CACHE["data"] = list(resultado)
    logger.info(f"Buscados {len(resultado)} clientes ativos Oracle (dedup por cd)")
    return resultado


def carregar_clientes_ativos(logger):
    agora = datetime.now()
    limite_max = agora
    limite_min = agora - timedelta(days=180)

    # Base local: clientes com ultimo_pedido_oracle entre 0-180 dias
    clientes_ativos_local = (
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
                Cliente.consultor_id,
                Cliente.proxima_ligacao,
                Cliente.data_ultima_sincronizacao,
            )
        )
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle >= limite_min,
            Cliente.ultimo_pedido_oracle <= limite_max,
        )
        .all()
    )

    # Enriquecer com dados do Oracle (centralizadora, validacao de codigos)
    centralizadora_por_cd = {}
    cache_quente = (
        _ATIVOS_ORACLE_ENRICH_CACHE["ts"] is not None
        and (datetime.now() - _ATIVOS_ORACLE_ENRICH_CACHE["ts"]) <= _ATIVOS_ORACLE_ENRICH_TTL
        and isinstance(_ATIVOS_ORACLE_ENRICH_CACHE["data"], dict)
    )
    codigos_ativos_oracle = set()
    oracle_enriquecimento_ok = False

    # Tentar snapshot do dia primeiro (mais rapido)
    snapshot = carregar_snapshot_ativos_oracle()
    if snapshot:
        codigos_ativos_oracle, centralizadora_por_cd = montar_mapa_snapshot_ativos(snapshot)
        centralizadora_por_cd["__codigos_ativos_oracle__"] = list(codigos_ativos_oracle)
        _ATIVOS_ORACLE_ENRICH_CACHE["ts"] = datetime.now()
        _ATIVOS_ORACLE_ENRICH_CACHE["data"] = dict(centralizadora_por_cd)
        oracle_enriquecimento_ok = bool(codigos_ativos_oracle)
    elif cache_quente:
        centralizadora_por_cd = dict(_ATIVOS_ORACLE_ENRICH_CACHE["data"])
        codigos_cache = centralizadora_por_cd.get("__codigos_ativos_oracle__") or []
        codigos_ativos_oracle = {
            str(cd or "").strip()
            for cd in codigos_cache
            if str(cd or "").strip()
        }
        oracle_enriquecimento_ok = bool(codigos_ativos_oracle)
    else:
        try:
            from oracle_service import get_clientes_ativos_oracle as _get_clientes_ativos_oracle

            ativos_oracle_raw = _get_clientes_ativos_oracle() or []
            for row in ativos_oracle_raw:
                cd = str(row.get("cd_cliente") or "").strip()
                if not cd or cd in centralizadora_por_cd:
                    continue
                codigos_ativos_oracle.add(cd)
                centralizadora_por_cd[cd] = {
                    "cd_centralizado": row.get("cd_centralizado"),
                    "nome_centralizadora": row.get("nome_centralizadora"),
                }
            centralizadora_por_cd["__codigos_ativos_oracle__"] = list(codigos_ativos_oracle)
            salvar_snapshot_ativos_oracle(ativos_oracle_raw)
            _ATIVOS_ORACLE_ENRICH_CACHE["ts"] = datetime.now()
            _ATIVOS_ORACLE_ENRICH_CACHE["data"] = dict(centralizadora_por_cd)
            oracle_enriquecimento_ok = True
        except Exception as e:
            logger.warning(f"Falha ao buscar clientes ativos via Oracle: {e}")

    # Fallback: se nao conseguiu codigos, tenta Oracle direto
    if not codigos_ativos_oracle:
        try:
            from oracle_service import get_clientes_ativos_oracle as _get_clientes_ativos_oracle

            ativos_oracle_raw = _get_clientes_ativos_oracle() or []
            for row in ativos_oracle_raw:
                cd = str(row.get("cd_cliente") or "").strip()
                if not cd:
                    continue
                codigos_ativos_oracle.add(cd)
                if cd not in centralizadora_por_cd:
                    centralizadora_por_cd[cd] = {
                        "cd_centralizado": row.get("cd_centralizado"),
                        "nome_centralizadora": row.get("nome_centralizadora"),
                    }
            if codigos_ativos_oracle:
                centralizadora_por_cd["__codigos_ativos_oracle__"] = list(codigos_ativos_oracle)
                salvar_snapshot_ativos_oracle(ativos_oracle_raw)
                _ATIVOS_ORACLE_ENRICH_CACHE["ts"] = datetime.now()
                _ATIVOS_ORACLE_ENRICH_CACHE["data"] = dict(centralizadora_por_cd)
                oracle_enriquecimento_ok = True
        except Exception as e:
            logger.warning(f"Falha ao validar codigos de ativos no Oracle: {e}")

    # Se Oracle respondeu, ele e a referencia para decidir quem esta ativo hoje.
    if codigos_ativos_oracle:
        clientes_ativos_local = [
            c for c in clientes_ativos_local
            if str(c.cd_cliente_oracle or "").strip() in codigos_ativos_oracle
        ]

    # Deduplicacao por codigo Oracle
    dedup_por_cd = {}
    for c in clientes_ativos_local:
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
    resultado = list(dedup_por_cd.values())

    origem_ref = "oracle+local" if (oracle_enriquecimento_ok or codigos_ativos_oracle) else "local"
    logger.info(f"Buscados {len(resultado)} clientes ativos ({origem_ref}, dedup por cd)")
    return resultado
