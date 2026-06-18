from datetime import datetime, timedelta

from routes.clientes_ligacoes.listagem_filters import corresponde_termo_busca


def filtrar_listas_por_termo(termo, pendentes, contatados, precisa_retornar):
    if not termo:
        return pendentes, contatados, precisa_retornar

    def _match_termo(item):
        return corresponde_termo_busca(
            termo,
            item,
            ("nome", "cnpj", "telefone", "telefone2", "representante_nome", "representante_oracle", "contato"),
        )

    pendentes_view = [c for c in pendentes if _match_termo(c)]
    contatados_view = [c for c in contatados if _match_termo(c)]
    precisa_retornar_view = [c for c in precisa_retornar if _match_termo(c)]
    return pendentes_view, contatados_view, precisa_retornar_view


def ordenar_clientes_por_aba(aba, pendentes_view, contatados_view, precisa_retornar_view, filtro):
    if aba == "pendentes":
        # A aba "pendentes" funciona como carteira operacional.
        # Mantemos o cliente visivel mesmo apos contato/retorno agendado,
        # enquanto as abas "contatados" e "retornar" continuam refletindo o status.
        carteira_por_id = {}
        for item in pendentes_view + contatados_view + precisa_retornar_view:
            cid = item.get("id")
            if cid and cid not in carteira_por_id:
                carteira_por_id[cid] = item
        carteira = list(carteira_por_id.values())
        return sorted(
            carteira,
            key=lambda x: (
                float(x.get("valor_total_365dias") or 0),
                float(x.get("valor_ultimo_pedido") or 0),
            ),
            reverse=True,
        )

    if aba == "retornar":
        agora = datetime.now()

        def _chave_retornar(item):
            proxima = item.get("proxima_ligacao")
            ultima = item.get("ultima_ligacao")
            atrasado = bool(proxima and proxima <= agora)
            sem_data_retorno = proxima is None
            return (
                1 if atrasado else 0,                         # atrasados primeiro
                0 if not sem_data_retorno else 1,             # com data antes de sem data
                proxima or datetime.max,                      # entre os com data, mais proximo primeiro
                -(ultima.timestamp() if ultima else 0),       # sem data: ultima ligacao mais recente primeiro
                -float(item.get("valor_total_365dias") or 0), # desempate
                -float(item.get("valor_ultimo_pedido") or 0), # desempate
            )

        return sorted(
            precisa_retornar_view,
            key=_chave_retornar,
        )

    clientes = sorted(
        contatados_view,
        key=lambda x: (
            1 if x.get("ultima_ligacao") else 0,
            x.get("ultima_ligacao") or datetime.min,
            float(x.get("valor_total_365dias") or 0),
            float(x.get("valor_ultimo_pedido") or 0),
        ),
        reverse=True,
    )
    if filtro == "antigos":
        limite = datetime.now() - timedelta(days=30)
        clientes = [c for c in clientes if c["ultima_ligacao"] and c["ultima_ligacao"] < limite]
    elif filtro == "recentes":
        limite = datetime.now() - timedelta(days=7)
        clientes = [c for c in clientes if c["ultima_ligacao"] and c["ultima_ligacao"] >= limite]
    return clientes
