def consultor_sem_permissao_no_cliente(current_user, cliente):
    return current_user.tipo == "consultor" and cliente.consultor_id != current_user.id


def consultor_sem_permissao_na_ligacao(current_user, ligacao):
    return current_user.tipo == "consultor" and ligacao.consultor_id != current_user.id
