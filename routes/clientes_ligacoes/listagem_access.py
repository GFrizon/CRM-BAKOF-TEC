from flask import flash, redirect, url_for

from routes.clientes_ligacoes.badges import (
    _total_oracle_badge_consultor_lista_oracle,
    _total_oracle_badge,
    _total_oracle_badge_supervisor_repr,
    _total_proximos_badge,
)
from routes.clientes_ligacoes.supervisor_repr import (
    contar_proximos_inativacao_supervisor_repr,
    obter_codigos_representantes_vinculados,
)


def preparar_contexto_inicial_listagem(request, current_user):
    if not current_user.is_authenticated:
        return {"response": redirect(url_for("login"))}

    if current_user.tipo not in ("consultor", "supervisor", "televendas", "supervisor_repr"):
        flash("Perfil sem acesso.", "danger")
        return {"response": redirect(url_for("index"))}

    if current_user.tipo == "televendas":
        aba_padrao = "inativos"
    elif current_user.tipo == "supervisor_repr":
        aba_padrao = "oracle"
    else:
        aba_padrao = "pendentes"

    aba = request.args.get("aba", aba_padrao)
    if current_user.tipo == "televendas":
        total_oracle_badge = 0
    elif current_user.tipo == "consultor":
        # Mantem badge alinhado com a mesma base/regra da aba Oracle.
        total_oracle_badge = _total_oracle_badge_consultor_lista_oracle(current_user.id)
    else:
        total_oracle_badge = _total_oracle_badge()
    total_proximos_badge = _total_proximos_badge(
        current_user.id if current_user.tipo in ("consultor", "televendas") else None
    )

    if current_user.tipo not in ("televendas", "supervisor") and aba == "inativos":
        aba_destino = "oracle" if current_user.tipo == "supervisor_repr" else "pendentes"
        return {"response": redirect(url_for("meus_clientes", aba=aba_destino))}

    if current_user.tipo == "televendas" and aba in ("pendentes", "oracle", "proximos_inativacao"):
        return {"response": redirect(url_for("meus_clientes", aba="inativos"))}

    if current_user.tipo == "supervisor_repr" and aba in ("pendentes", "contatados", "retornar"):
        return {"response": redirect(url_for("meus_clientes", aba="oracle"))}

    apenas_meus = True if current_user.tipo in ("consultor", "televendas") else (request.args.get("meus") == "1")

    codigos_representantes_vinculados = []
    if current_user.tipo == "supervisor_repr":
        codigos_representantes_vinculados = obter_codigos_representantes_vinculados(current_user.id)
        if not codigos_representantes_vinculados:
            flash(
                "Nenhum representante vinculado a este supervisor. Entre em contato com o administrador.",
                "warning",
            )

        total_proximos_badge = contar_proximos_inativacao_supervisor_repr(
            codigos_representantes_vinculados
        )
        total_oracle_badge = _total_oracle_badge_supervisor_repr(
            codigos_representantes_vinculados
        )

    return {
        "response": None,
        "aba": aba,
        "total_oracle_badge": total_oracle_badge,
        "total_proximos_badge": total_proximos_badge,
        "apenas_meus": apenas_meus,
        "codigos_representantes_vinculados": codigos_representantes_vinculados,
    }
