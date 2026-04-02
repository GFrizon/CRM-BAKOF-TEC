from flask import jsonify, request
from flask_login import current_user


ROTAS_ESCRITA_BLOQUEADAS_SUPERVISOR_REPR = {
    "preencher_cliente_oracle_por_cnpj",
    "sincronizar_cliente_oracle_por_id",
    "sincronizar_clientes_manuais_oracle",
    "criar_cliente_manual",
    "iniciar_contato_cliente",
    "registrar_ligacao",
    "editar_observacao",
    "editar_ligacao",
    "adicionar_nota",
    "limpar_clientes_consultor",
}


def bloquear_escrita_supervisor_repr():
    if not current_user.is_authenticated:
        return None
    if current_user.tipo != "supervisor_repr":
        return None
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if request.endpoint in ROTAS_ESCRITA_BLOQUEADAS_SUPERVISOR_REPR:
        return jsonify(
            {
                "ok": False,
                "mensagem": "Perfil Supervisor de Representante possui acesso somente leitura.",
            }
        ), 403
    return None
