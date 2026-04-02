from datetime import datetime

from core.extensions import db
from core.helpers import s
from core.models import Cliente, Ligacao
from routes.clientes_ligacoes.ligacao_helpers import (
    calcular_proxima_ligacao,
    mensagem_sucesso_ligacao,
    normalizar_resultado_ligacao,
    parse_valor_venda,
)
from routes.clientes_ligacoes.permission_helpers import consultor_sem_permissao_no_cliente


def registrar_ligacao_service(current_user, cliente_id, payload):
    obs = s(payload.get("observacao"))
    contato_nome = s(payload.get("contato_nome"))
    resultado = normalizar_resultado_ligacao(s(payload.get("resultado") or "nao_comprou"))
    valor_venda = parse_valor_venda(payload.get("valor_venda"))

    cli = db.session.get(Cliente, cliente_id)
    if not cli:
        return {"ok": False, "mensagem": "Cliente não encontrado."}, 404

    if consultor_sem_permissao_no_cliente(current_user, cli):
        return {"ok": False, "mensagem": "Sem permissão para este cliente."}, 403

    agora = datetime.now()

    lig = Ligacao(
        cliente_id=cliente_id,
        consultor_id=current_user.id,
        data_hora=agora,
        observacao=obs or None,
        contato_nome=contato_nome or None,
        resultado=resultado,
        valor_venda=valor_venda,
    )
    db.session.add(lig)

    if current_user.tipo == "televendas" and cli.consultor_id != current_user.id:
        cli.consultor_id = current_user.id

    data_retorno = s(payload.get("data_retorno"))
    cli.proxima_ligacao = calcular_proxima_ligacao(
        agora=agora,
        resultado=resultado,
        data_retorno_raw=data_retorno,
        dias_retorno_raw=payload.get("dias_retorno"),
    )

    cli.em_atendimento_por = None
    cli.em_atendimento_ate = None
    db.session.commit()

    msg = mensagem_sucesso_ligacao(resultado, cli.proxima_ligacao)
    return {
        "ok": True,
        "mensagem": msg,
        "proxima_ligacao": cli.proxima_ligacao.isoformat() if cli.proxima_ligacao else None,
    }, 200
