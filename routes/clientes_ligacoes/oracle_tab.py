from datetime import datetime, date, timedelta


_ORACLE_90_150_CACHE = {}
_ORACLE_90_150_CACHE_TTL = timedelta(minutes=5)


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


def carregar_clientes_oracle_deduplicados(logger, periodo_oracle):
    if periodo_oracle and periodo_oracle not in ("90", "150", "180"):
        logger.warning(f"Valor invalido para periodo_oracle: {periodo_oracle}")

    cache_key = str(periodo_oracle or "default")
    cache_item = _ORACLE_90_150_CACHE.get(cache_key)
    if cache_item:
        idade = datetime.now() - cache_item["ts"]
        if idade <= _ORACLE_90_150_CACHE_TTL:
            # Copia rasa para evitar mutacao acidental do cache por chamadas futuras.
            return list(cache_item["data"])

    try:
        from oracle_service import get_clientes_oracle

        clientes_oracle_raw = get_clientes_oracle()
        logger.info(f"Buscados {len(clientes_oracle_raw)} clientes Oracle (90-150d)")
    except Exception as e:
        logger.error(f"Erro ao buscar clientes Oracle: {e}")
        clientes_oracle_raw = []

    # Garante 1 linha por cliente (ultimo pedido) caso o Oracle retorne repetidos.
    clientes_oracle_por_cd = {}
    for row in clientes_oracle_raw:
        cd = str(row.get("cd_cliente") or "").strip()
        if not cd:
            continue
        atual = clientes_oracle_por_cd.get(cd)
        if not atual:
            clientes_oracle_por_cd[cd] = row
            continue
        dt_novo = row.get("dt_pedido")
        dt_atual = atual.get("dt_pedido")
        if dt_novo and (not dt_atual or dt_novo > dt_atual):
            clientes_oracle_por_cd[cd] = row

    clientes_deduplicados = list(clientes_oracle_por_cd.values())
    clientes_filtrados = _filtrar_por_periodo(clientes_deduplicados, periodo_oracle)
    _ORACLE_90_150_CACHE[cache_key] = {
        "ts": datetime.now(),
        "data": clientes_filtrados,
    }
    return list(clientes_filtrados)

