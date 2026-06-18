from datetime import datetime, date, timedelta

from services.oracle_snapshot_service import (
    carregar_snapshot_oracle_90_150,
    rows_snapshot_oracle_90_150,
    salvar_snapshot_oracle_90_150,
)


_ORACLE_90_150_CACHE = {}
_ORACLE_90_150_CACHE_TTL = timedelta(hours=24)


def limpar_cache_clientes_oracle():
    _ORACLE_90_150_CACHE.clear()


def _coagir_data_pedido(valor):
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day, 0, 0, 0)
    txt = str(valor).strip()
    if not txt:
        return None
    formatos = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    )
    for fmt in formatos:
        try:
            return datetime.strptime(txt, fmt)
        except Exception:
            continue
    return None


def _filtrar_por_periodo(clientes, periodo_oracle):
    if not periodo_oracle:
        return list(clientes or [])
    try:
        dias = int(periodo_oracle)
    except Exception:
        return list(clientes or [])

    limite = datetime.now() - timedelta(days=dias)
    filtrados = []
    for row in clientes or []:
        dt = _coagir_data_pedido((row or {}).get("dt_pedido"))
        if dt and dt <= limite:
            filtrados.append(row)
    return filtrados


def _deduplicar_rows(rows):
    por_cd = {}
    for row in rows or []:
        cd = str(row.get("cd_cliente") or "").strip()
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
    return list(por_cd.values())


def carregar_clientes_oracle_deduplicados(logger, periodo_oracle):
    if periodo_oracle and periodo_oracle not in ("90", "150", "180"):
        logger.warning(f"Valor invalido para periodo_oracle: {periodo_oracle}")

    cache_key = str(periodo_oracle or "default")
    cache_item = _ORACLE_90_150_CACHE.get(cache_key)
    if cache_item:
        idade = datetime.now() - cache_item["ts"]
        if idade <= _ORACLE_90_150_CACHE_TTL:
            return list(cache_item["data"])

    # Tentar snapshot do dia primeiro (mais rapido)
    snapshot = carregar_snapshot_oracle_90_150()
    if snapshot:
        clientes_raw = rows_snapshot_oracle_90_150(snapshot)
        clientes_deduplicados = _deduplicar_rows(clientes_raw)
        clientes_filtrados = _filtrar_por_periodo(clientes_deduplicados, periodo_oracle)
        _ORACLE_90_150_CACHE[cache_key] = {
            "ts": datetime.now(),
            "data": clientes_filtrados,
        }
        logger.info(
            "[oracle_tab] %s clientes 90-150d snapshot data_ref=%s atualizado_em=%s",
            len(clientes_filtrados),
            snapshot.get("data_ref"),
            snapshot.get("atualizado_em"),
        )
        return list(clientes_filtrados)

    # Fallback: buscar direto no Oracle e salvar snapshot
    try:
        from oracle_service import get_clientes_oracle

        clientes_oracle_raw = get_clientes_oracle()
        logger.info(f"Buscados {len(clientes_oracle_raw)} clientes Oracle (90-150d)")
    except Exception as e:
        logger.error(f"Erro ao buscar clientes Oracle: {e}")
        clientes_oracle_raw = []

    clientes_deduplicados = _deduplicar_rows(clientes_oracle_raw)
    salvar_snapshot_oracle_90_150(clientes_deduplicados)
    clientes_filtrados = _filtrar_por_periodo(clientes_deduplicados, periodo_oracle)
    _ORACLE_90_150_CACHE[cache_key] = {
        "ts": datetime.now(),
        "data": clientes_filtrados,
    }
    return list(clientes_filtrados)
