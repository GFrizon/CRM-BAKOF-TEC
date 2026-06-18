from flask import jsonify, request, session
from flask_login import current_user
from core.config import SUPERVISOR_SECRET_HEALTH_KEY


ROTAS_ESCRITA_BLOQUEADAS_PERFIL_SOMENTE_LEITURA = {
    "preencher_cliente_oracle_por_cnpj",
    "sincronizar_cliente_oracle_por_id",
    "sincronizar_clientes_manuais_oracle",
    "criar_cliente_manual",
    "iniciar_contato_cliente",
    "registrar_ligacao",
    "editar_observacao",
    "editar_ligacao",
    "adicionar_nota",
    "registrar_venda_retroativa",
    "remover_cliente",
    "limpar_clientes_consultor",
}


def _usuario_somente_leitura():
    return bool(
        getattr(current_user, "is_authenticated", False)
        and getattr(current_user, "tipo", "") in ("supervisor_repr", "representante")
    )


def bloquear_escrita_perfil_somente_leitura():
    if not current_user.is_authenticated:
        return None
    if not _usuario_somente_leitura():
        return None
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if request.endpoint in ROTAS_ESCRITA_BLOQUEADAS_PERFIL_SOMENTE_LEITURA:
        return jsonify(
            {
                "ok": False,
                "mensagem": "Perfil possui acesso somente leitura.",
            }
        ), 403
    return None


def resposta_supervisor_repr_somente_leitura(mensagem):
    return jsonify({"ok": False, "mensagem": mensagem}), 403


def resposta_representante_somente_leitura(mensagem):
    return jsonify({"ok": False, "mensagem": mensagem}), 403


def obter_chave_sessao_supervisor_dev():
    chave = str(SUPERVISOR_SECRET_HEALTH_KEY or "").strip()
    if not chave:
        return None
    return f"dev_panel_ok::{chave}"


def supervisor_dev_liberado():
    if not current_user.is_authenticated:
        return False
    if current_user.tipo != "supervisor":
        return False
    sess_key = obter_chave_sessao_supervisor_dev()
    if not sess_key:
        return False
    return bool(session.get(sess_key))


def resposta_supervisor_dev_obrigatorio(
    mensagem="Acao disponivel apenas no modo dev do supervisor."
):
    return jsonify({"ok": False, "mensagem": mensagem}), 403
