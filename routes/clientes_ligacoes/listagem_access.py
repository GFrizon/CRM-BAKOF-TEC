from flask import flash, redirect, url_for

from routes.clientes_ligacoes.badges import (
    _total_oracle_badge_consultor_lista_oracle,
    _total_oracle_badge,
    _total_oracle_badge_supervisor_repr,
    _total_oracle_badge_supervisor_lista_oracle,
    _total_proximos_badge,
    _total_ativos_badge,
    _total_ativos_badge_representante,
    _total_construtoras_badge,
    _total_construtoras_badge_representante,
)
from routes.clientes_ligacoes.supervisor_repr import (
    contar_proximos_inativacao_supervisor_repr,
    obter_codigos_representantes_vinculados,
)
from routes.clientes_ligacoes.domain_utils import _normalizar_codigo_representante


def preparar_contexto_inicial_listagem(request, current_user):
    if not current_user.is_authenticated:
        return {"response": redirect(url_for("login"))}

    if current_user.tipo not in ("consultor", "supervisor", "televendas", "supervisor_repr", "representante"):
        flash("Perfil sem acesso.", "danger")
        return {"response": redirect(url_for("index"))}

    if current_user.tipo == "televendas":
        aba_padrao = "inativos"
    elif current_user.tipo in ("supervisor_repr", "representante"):
        aba_padrao = "oracle"
    else:
        aba_padrao = "pendentes"

    aba = request.args.get("aba", aba_padrao)
    visao = (request.args.get("visao") or "").strip().lower()
    if visao not in ("dashboard", "clientes"):
        if current_user.tipo in ("consultor", "televendas", "supervisor_repr", "representante"):
            visao = "dashboard"
        else:
            visao = "clientes"
    dashboard_tipo = (request.args.get("dashboard_tipo") or "").strip().lower()
    if dashboard_tipo not in ("consultor", "televendas"):
        dashboard_tipo = None
    if current_user.tipo != "supervisor":
        dashboard_tipo = None
    if current_user.tipo == "televendas":
        total_oracle_badge = 0
    elif current_user.tipo == "consultor":
        # Mantem badge alinhado com a mesma base/regra da aba Oracle.
        total_oracle_badge = _total_oracle_badge_consultor_lista_oracle(current_user.id)
    elif current_user.tipo == "supervisor":
        # Supervisor com dashboard "televendas" nao usa campanha 90-150d.
        if dashboard_tipo == "televendas":
            total_oracle_badge = 0
        else:
            # Usa a mesma fonte da aba Oracle (snapshot) para garantir numero consistente.
            total_oracle_badge = _total_oracle_badge_supervisor_lista_oracle()
    else:
        total_oracle_badge = _total_oracle_badge()
    if current_user.tipo == "supervisor" and dashboard_tipo == "televendas":
        total_proximos_badge = 0
    else:
        total_proximos_badge = _total_proximos_badge(
            current_user.id if current_user.tipo in ("consultor", "televendas") else None
        )

    total_construtoras_badge = (
        _total_construtoras_badge()
        if current_user.tipo in ("televendas", "supervisor")
        else 0
    )

    total_ativos_badge = _total_ativos_badge(
        current_user.id if current_user.tipo in ("consultor",) else None
    )

    if current_user.tipo not in ("televendas", "supervisor", "consultor", "representante") and aba in ("inativos", "construtoras"):
        aba_destino = "oracle" if current_user.tipo in ("supervisor_repr", "representante") else "pendentes"
        return {"response": redirect(url_for("meus_clientes", aba=aba_destino, visao=visao))}
    if current_user.tipo == "consultor" and aba == "construtoras":
        return {"response": redirect(url_for("meus_clientes", aba="pendentes", visao=visao))}

    if current_user.tipo == "televendas" and aba in ("pendentes", "oracle", "proximos_inativacao"):
        return {"response": redirect(url_for("meus_clientes", aba="inativos", visao=visao))}

    if current_user.tipo == "supervisor":
        # Mundo televendas: carteiras de televendas e retornos.
        if dashboard_tipo == "televendas" and aba in ("pendentes", "oracle", "proximos_inativacao"):
            return {
                "response": redirect(
                    url_for("meus_clientes", aba="inativos", dashboard_tipo="televendas", visao=visao)
                )
            }
        # Mundo consultores: manter acesso as carteiras globais que compoem o dashboard.

    if current_user.tipo == "representante" and aba == "retornar":
        return {"response": redirect(url_for("meus_clientes", aba="pendentes", visao=visao))}

    if current_user.tipo == "supervisor_repr" and aba in ("pendentes", "contatados", "retornar"):
        return {"response": redirect(url_for("meus_clientes", aba="oracle", visao=visao))}

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
    elif current_user.tipo == "representante":
        codigo_representante = _normalizar_codigo_representante(
            getattr(current_user, "codigo_representante", "")
        )
        codigos_representantes_vinculados = [codigo_representante] if codigo_representante else []
        if not codigos_representantes_vinculados:
            flash(
                "Usuario representante sem codigo vinculado. Entre em contato com o administrador.",
                "warning",
            )
        total_proximos_badge = contar_proximos_inativacao_supervisor_repr(
            codigos_representantes_vinculados
        )
        total_oracle_badge = _total_oracle_badge_supervisor_repr(
            codigos_representantes_vinculados
        )
        total_ativos_badge = _total_ativos_badge_representante(
            codigos_representantes_vinculados
        )
        total_construtoras_badge = _total_construtoras_badge_representante(
            codigos_representantes_vinculados
        )

    return {
        "response": None,
        "aba": aba,
        "visao": visao,
        "dashboard_tipo": dashboard_tipo,
        "total_oracle_badge": total_oracle_badge,
        "total_proximos_badge": total_proximos_badge,
        "total_ativos_badge": total_ativos_badge,
        "total_construtoras_badge": total_construtoras_badge,
        "apenas_meus": apenas_meus,
        "codigos_representantes_vinculados": codigos_representantes_vinculados,
    }
