from sqlalchemy.orm import joinedload

from core.extensions import db
from core.models import Cliente, Ligacao
from routes.clientes_ligacoes.cache_invalidation import invalidar_caches_listagens_clientes
from routes.clientes_ligacoes.interaction_serializers import (
    serializar_detalhes_ligacao,
    serializar_historico_ligacoes,
)
from routes.clientes_ligacoes.ligacao_helpers import aplicar_payload_edicao_ligacao
from routes.clientes_ligacoes.permission_helpers import (
    consultor_sem_permissao_na_ligacao,
    consultor_sem_permissao_no_cliente,
)


def editar_observacao_ligacao_service(ligacao_id, current_user, observacao, normalizador_texto):
    ligacao = db.session.get(Ligacao, ligacao_id)
    if not ligacao:
        return {"ok": False, "mensagem": "Ligação não encontrada"}, 404

    if consultor_sem_permissao_na_ligacao(current_user, ligacao):
        return {"ok": False, "mensagem": "Sem permissão"}, 403

    ligacao.observacao = normalizador_texto(observacao) or None
    db.session.commit()
    invalidar_caches_listagens_clientes("edicao de observacao de ligacao")
    return {"ok": True, "mensagem": "Observação atualizada com sucesso!"}, 200


def editar_ligacao_service(ligacao_id, current_user, payload, normalizador_texto):
    ligacao = db.session.get(Ligacao, ligacao_id)
    if not ligacao:
        return {"ok": False, "mensagem": "Ligação não encontrada"}, 404

    if consultor_sem_permissao_na_ligacao(current_user, ligacao):
        return {"ok": False, "mensagem": "Sem permissão para editar esta ligação"}, 403

    aplicar_payload_edicao_ligacao(ligacao, payload or {}, normalizador_texto)
    db.session.commit()
    invalidar_caches_listagens_clientes("edicao de ligacao")
    return {"ok": True, "mensagem": "Ligação atualizada com sucesso!"}, 200


def detalhes_ligacao_service(ligacao_id, current_user, formatar_dinheiro_fn):
    ligacao = db.session.get(Ligacao, ligacao_id)
    if not ligacao:
        return {"erro": "Ligação não encontrada"}, 404

    if consultor_sem_permissao_na_ligacao(current_user, ligacao):
        return {"erro": "Sem permissão"}, 403

    return serializar_detalhes_ligacao(ligacao, formatar_dinheiro_fn), 200


def historico_ligacoes_service(cliente_id, current_user, normalizador_texto, formatar_dinheiro_fn):
    cli = db.session.get(Cliente, cliente_id)
    if not cli:
        return []

    if consultor_sem_permissao_no_cliente(current_user, cli):
        return []

    query = Ligacao.query.options(joinedload(Ligacao.consultor))
    cd_cliente_oracle = str(cli.cd_cliente_oracle or "").strip()
    if cd_cliente_oracle:
        ids_mesmo_codigo = [
            cid
            for (cid,) in (
                db.session.query(Cliente.id)
                .filter(Cliente.cd_cliente_oracle == cd_cliente_oracle)
                .all()
            )
            if cid
        ]
        if ids_mesmo_codigo:
            query = query.filter(Ligacao.cliente_id.in_(ids_mesmo_codigo))
        else:
            query = query.filter(Ligacao.cliente_id == cliente_id)
    else:
        query = query.filter(Ligacao.cliente_id == cliente_id)

    regs = query.order_by(Ligacao.data_hora.desc(), Ligacao.id.desc()).all()
    return serializar_historico_ligacoes(
        registros=regs,
        current_user_tipo=current_user.tipo,
        current_user_id=current_user.id,
        normalizador_texto=normalizador_texto,
        formatar_dinheiro_fn=formatar_dinheiro_fn,
    )
