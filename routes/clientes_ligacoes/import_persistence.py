from datetime import datetime

from sqlalchemy import text

from core.extensions import db
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


def flush_batch_clientes(batch_clientes, logger, erros):
    try:
        db.session.add_all(batch_clientes)
        db.session.flush()
        logger.info(f"Processado batch de {len(batch_clientes)} clientes")
        return []
    except Exception as batch_error:
        logger.error(f"Erro no batch processing: {str(batch_error)}")
        db.session.rollback()
        for cliente in batch_clientes:
            try:
                db.session.add(cliente)
                db.session.flush()
            except Exception as single_error:
                logger.warning(f"Erro em cliente individual: {str(single_error)}")
                erros.append(f"Erro ao inserir cliente: {str(single_error)}")
        return []


def flush_batch_final_clientes(batch_clientes, logger, erros):
    if not batch_clientes:
        return
    try:
        db.session.add_all(batch_clientes)
        logger.info(f"Processado batch final de {len(batch_clientes)} clientes")
    except Exception as final_batch_error:
        logger.error(f"Erro no batch final: {str(final_batch_error)}")
        db.session.rollback()
        for cliente in batch_clientes:
            try:
                db.session.add(cliente)
                db.session.flush()
            except Exception as single_error:
                logger.warning(f"Erro em cliente individual final: {str(single_error)}")
                erros.append(f"Erro ao inserir cliente final: {str(single_error)}")


def registrar_importacao(arquivo_nome, consultor_id, total_inseridos, logger):
    try:
        imp_nome = arquivo_nome or "upload"
        db.session.execute(
            text(
                "INSERT INTO importacoes (arquivo_nome, consultor_id, registros_importados, data_importacao) "
                "VALUES (:n, :c, :r, :d)"
            ),
            {"n": imp_nome, "c": consultor_id, "r": total_inseridos, "d": datetime.now()},
        )
    except Exception as import_error:
        logger.warning(f"Erro ao registrar importação: {str(import_error)}")
