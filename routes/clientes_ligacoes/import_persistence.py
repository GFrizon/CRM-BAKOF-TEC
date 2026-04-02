from core.models import Cliente


def atualizar_ou_reativar_cliente_importado(empresa_cnpj, nome_cliente, telefone, representante, consultor_id):
    existente_ativo = Cliente.query.filter_by(cnpj=empresa_cnpj, ativo=True).first()
    if existente_ativo:
        mudou = False
        if telefone and (not existente_ativo.telefone or existente_ativo.telefone != telefone):
            existente_ativo.telefone = telefone
            mudou = True
        if nome_cliente and nome_cliente != existente_ativo.nome:
            existente_ativo.nome = nome_cliente[:200]
            mudou = True
        if representante and representante != existente_ativo.representante_nome:
            existente_ativo.representante_nome = representante[:200]
            mudou = True
        if consultor_id and existente_ativo.consultor_id != consultor_id:
            existente_ativo.consultor_id = consultor_id
            mudou = True
        if existente_ativo.origem != "importado_csv":
            existente_ativo.origem = "importado_csv"
            mudou = True
        return "atualizado" if mudou else "inalterado"

    existente_inativo = Cliente.query.filter_by(cnpj=empresa_cnpj, ativo=False).first()
    if existente_inativo:
        existente_inativo.nome = nome_cliente[:200] or existente_inativo.nome
        existente_inativo.telefone = telefone
        existente_inativo.representante_nome = (representante[:200] or None)
        existente_inativo.consultor_id = consultor_id
        existente_inativo.ativo = True
        existente_inativo.origem = "importado_csv"
        return "reativado"

    return None


def construir_cliente_importado(nome_cliente, empresa_cnpj, telefone, representante, consultor_id):
    return Cliente(
        nome=nome_cliente[:200],
        cnpj=(empresa_cnpj[:18] or None),
        telefone=telefone,
        representante_nome=(representante[:200] or None),
        consultor_id=consultor_id,
        ativo=True,
        origem="importado_csv",
    )
