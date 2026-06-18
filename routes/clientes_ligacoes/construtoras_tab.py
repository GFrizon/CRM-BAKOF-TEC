from datetime import datetime, timedelta

from services.construtoras_snapshot_service import (
    carregar_snapshot_construtoras_oracle,
    rows_snapshot_construtoras,
    salvar_snapshot_construtoras_oracle,
)


_CONSTRUTORAS_CACHE = {
    "ts": None,
    "data": None,
}
_CONSTRUTORAS_CACHE_TTL = timedelta(hours=24)


def limpar_cache_clientes_construtoras():
    _CONSTRUTORAS_CACHE["ts"] = None
    _CONSTRUTORAS_CACHE["data"] = None


def _deduplicar_por_codigo(rows):
    por_cd = {}
    for row in rows or []:
        cd = str((row or {}).get("cd_cliente") or "").strip()
        if not cd:
            continue
        atual = por_cd.get(cd)
        if not atual:
            por_cd[cd] = row
            continue
        dt_novo = (row or {}).get("dt_pedido")
        dt_atual = (atual or {}).get("dt_pedido")
        if dt_novo and (not dt_atual or dt_novo > dt_atual):
            por_cd[cd] = row
    return list(por_cd.values())


def carregar_clientes_construtoras_deduplicados(logger):
    cache_quente = (
        _CONSTRUTORAS_CACHE["ts"] is not None
        and (datetime.now() - _CONSTRUTORAS_CACHE["ts"]) <= _CONSTRUTORAS_CACHE_TTL
        and isinstance(_CONSTRUTORAS_CACHE["data"], list)
    )
    if cache_quente:
        return list(_CONSTRUTORAS_CACHE["data"])

    snapshot = carregar_snapshot_construtoras_oracle()
    if snapshot:
        rows = rows_snapshot_construtoras(snapshot)
        clientes = _deduplicar_por_codigo(rows)
        _CONSTRUTORAS_CACHE["ts"] = datetime.now()
        _CONSTRUTORAS_CACHE["data"] = list(clientes)
        logger.info(
            "Buscados %s clientes construtoras snapshot data_ref=%s atualizado_em=%s",
            len(clientes),
            snapshot.get("data_ref"),
            snapshot.get("atualizado_em"),
        )
        return list(clientes)

    try:
        from oracle_service import get_clientes_construtoras_oracle

        rows = get_clientes_construtoras_oracle() or []
        clientes = _deduplicar_por_codigo(rows)
        salvar_snapshot_construtoras_oracle(clientes)
        _CONSTRUTORAS_CACHE["ts"] = datetime.now()
        _CONSTRUTORAS_CACHE["data"] = list(clientes)
        logger.info(f"Buscados {len(clientes)} clientes construtoras (Oracle)")
        return list(clientes)
    except Exception as e:
        logger.warning(f"Falha ao buscar clientes construtoras no Oracle: {e}")
        return []
