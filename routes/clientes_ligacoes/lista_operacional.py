from datetime import datetime, timedelta


def filtrar_listas_por_termo(termo, pendentes, contatados, precisa_retornar):
    if not termo:
        return pendentes, contatados, precisa_retornar

    termo_lower = termo.lower()

    def _match_termo(item):
        return any(
            termo_lower in str(item.get(chave) or "").lower()
            for chave in ("nome", "cnpj", "telefone", "representante_nome", "representante_oracle")
        )

    pendentes_view = [c for c in pendentes if _match_termo(c)]
    contatados_view = [c for c in contatados if _match_termo(c)]
    precisa_retornar_view = [c for c in precisa_retornar if _match_termo(c)]
    return pendentes_view, contatados_view, precisa_retornar_view


def ordenar_clientes_por_aba(aba, pendentes_view, contatados_view, precisa_retornar_view, filtro):
    if aba == "pendentes":
        return sorted(
            pendentes_view,
            key=lambda x: (
                float(x.get("valor_total_365dias") or 0),
                float(x.get("valor_ultimo_pedido") or 0),
            ),
            reverse=True,
        )

    if aba == "retornar":
        return sorted(
            precisa_retornar_view,
            key=lambda x: (
                x["proxima_ligacao"] or datetime.max,
                float(x.get("valor_total_365dias") or 0),
                float(x.get("valor_ultimo_pedido") or 0),
            ),
        )

    clientes = sorted(
        contatados_view,
        key=lambda x: (
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
