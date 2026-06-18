def serializar_detalhes_ligacao(ligacao, formatar_dinheiro_fn):
    return {
        "id": ligacao.id,
        "resultado": ligacao.resultado,
        "valor_venda": float(ligacao.valor_venda or 0),
        "valor_venda_fmt": formatar_dinheiro_fn(ligacao.valor_venda),
        "observacao": ligacao.observacao,
        "contato_nome": ligacao.contato_nome,
        "data_hora": ligacao.data_hora.strftime("%d/%m/%Y %H:%M") if ligacao.data_hora else "",
    }


def serializar_historico_ligacoes(registros, current_user_tipo, current_user_id, normalizador_texto, formatar_dinheiro_fn):
    out = []
    for r in registros:
        try:
            dt = r.data_hora.strftime("%d/%m/%Y %H:%M") if r.data_hora else ""
            consultor_nome = r.consultor.nome if getattr(r, "consultor", None) else ""
            contato = normalizador_texto(r.contato_nome)
            resultado = normalizador_texto(r.resultado)
            try:
                valor_num = float(r.valor_venda or 0)
            except Exception:
                valor_num = 0.0

            out.append(
                {
                    "id": r.id,
                    "data_hora": dt,
                    "consultor": consultor_nome,
                    "contato_nome": contato,
                    "resultado": resultado,
                    "valor_venda": formatar_dinheiro_fn(valor_num),
                    "observacao": normalizador_texto(r.observacao),
                    "pode_editar": (current_user_tipo == "supervisor" or r.consultor_id == current_user_id),
                }
            )
        except Exception:
            continue
    return out


def serializar_notas(notas):
    return [
        {
            "id": n.id,
            "autor": n.usuario.nome if n.usuario else "",
            "texto": n.texto,
            "quando": n.data_criacao.strftime("%d/%m/%Y %H:%M"),
        }
        for n in notas
    ]
