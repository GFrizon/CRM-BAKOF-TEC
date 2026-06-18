from core.models import Cliente


def inativar_clientes_do_consultor(consultor_id):
    clientes = Cliente.query.filter_by(consultor_id=consultor_id, ativo=True).all()
    for cli in clientes:
        cli.ativo = False
    return len(clientes)
