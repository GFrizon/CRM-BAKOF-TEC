def carregar_clientes_oracle_deduplicados(logger, periodo_oracle):
    if periodo_oracle and periodo_oracle not in ("90", "120"):
        logger.warning(f"Valor invalido para periodo_oracle: {periodo_oracle}")

    try:
        from oracle_service import get_clientes_oracle

        clientes_oracle_raw = get_clientes_oracle()
        logger.info(f"Buscados {len(clientes_oracle_raw)} clientes Oracle (90-120d)")
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

    return list(clientes_oracle_por_cd.values())
