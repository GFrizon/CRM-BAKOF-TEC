from core.extensions import db
from core.models import Cliente
from routes.clientes_ligacoes.nota_helpers import buscar_notas_cliente, criar_nota_cliente
from routes.clientes_ligacoes.permission_helpers import consultor_sem_permissao_no_cliente


def listar_notas_service(cliente_id):
    notas = buscar_notas_cliente(cliente_id)
    return notas


def adicionar_nota_service(cliente_id, current_user, texto):
    cli = db.session.get(Cliente, cliente_id)
    if not cli:
        return {"ok": False, "mensagem": "Cliente não encontrado"}, 404

    if consultor_sem_permissao_no_cliente(current_user, cli):
        return {"ok": False, "mensagem": "Sem permissão"}, 403

    criar_nota_cliente(cliente_id=cliente_id, usuario_id=current_user.id, texto=texto)
    return {"ok": True, "mensagem": "Nota adicionada!"}, 200
