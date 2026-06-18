from core.extensions import db
from core.models import Cliente
from routes.clientes_ligacoes.lock_helpers import tentar_assumir_lock_cliente
from routes.clientes_ligacoes.permission_helpers import consultor_sem_permissao_no_cliente


def iniciar_contato_service(current_user, cliente_id, payload):
    forcar = bool(payload.get("forcar"))
    aba_contexto = str(payload.get("aba") or "").strip().lower()
    cd_oracle_payload = str(payload.get("cd_cliente_oracle") or "").strip()

    cli = db.session.get(Cliente, cliente_id)
    if not cli:
        return {"ok": False, "mensagem": "Cliente no encontrado."}, 404

    if consultor_sem_permissao_no_cliente(current_user, cli):
        return {"ok": False, "mensagem": "Sem permisso para este cliente."}, 403

    ok_lock, conflito = tentar_assumir_lock_cliente(
        cli=cli,
        current_user_id=current_user.id,
        aba_contexto=aba_contexto,
        cd_oracle_payload=cd_oracle_payload,
        forcar=forcar,
    )
    if not ok_lock and conflito:
        return conflito, 409

    db.session.commit()
    return {
        "ok": True,
        "em_atendimento_por_id": cli.em_atendimento_por,
        "em_atendimento_por_nome": current_user.nome,
        "em_atendimento_ate": (cli.em_atendimento_ate.strftime("%d/%m/%Y %H:%M") if cli.em_atendimento_ate else None),
        "forcado": bool(forcar),
    }, 200
