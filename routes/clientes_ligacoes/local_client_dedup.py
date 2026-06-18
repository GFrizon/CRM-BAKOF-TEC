from datetime import datetime


def escolher_melhor_cliente_por_codigo(clientes, stats_ligacoes_por_cliente_id=None):
    """Deduplica clientes locais por cd_cliente_oracle usando a mesma regra das listagens.

    Prioriza o registro com historico de ligacoes/retorno agendado. Em empate,
    usa pedido mais recente; depois sincronizacao mais recente; por ultimo,
    o maior id.
    """
    stats_ligacoes_por_cliente_id = stats_ligacoes_por_cliente_id or {}
    dedup_por_cd = {}
    for cliente in list(clientes or []):
        cd = str(getattr(cliente, "cd_cliente_oracle", None) or "").strip()
        if not cd:
            continue
        atual = dedup_por_cd.get(cd)
        if not atual:
            dedup_por_cd[cd] = cliente
            continue

        atual_id = int(getattr(atual, "id", 0) or 0)
        novo_id = int(getattr(cliente, "id", 0) or 0)
        atual_stats = stats_ligacoes_por_cliente_id.get(atual_id, {})
        novo_stats = stats_ligacoes_por_cliente_id.get(novo_id, {})
        atual_key = (
            int(atual_stats.get("total_ligacoes") or 0),
            atual_stats.get("ultima_ligacao") or datetime.min,
            1 if getattr(atual, "proxima_ligacao", None) else 0,
            getattr(atual, "proxima_ligacao", None) or datetime.min,
            1 if getattr(atual, "ativo", False) else 0,
            getattr(atual, "ultimo_pedido_oracle", None) or datetime.min,
            getattr(atual, "data_ultima_sincronizacao", None) or datetime.min,
            atual_id,
        )
        novo_key = (
            int(novo_stats.get("total_ligacoes") or 0),
            novo_stats.get("ultima_ligacao") or datetime.min,
            1 if getattr(cliente, "proxima_ligacao", None) else 0,
            getattr(cliente, "proxima_ligacao", None) or datetime.min,
            1 if getattr(cliente, "ativo", False) else 0,
            getattr(cliente, "ultimo_pedido_oracle", None) or datetime.min,
            getattr(cliente, "data_ultima_sincronizacao", None) or datetime.min,
            novo_id,
        )
        if novo_key > atual_key:
            dedup_por_cd[cd] = cliente
    return dedup_por_cd
