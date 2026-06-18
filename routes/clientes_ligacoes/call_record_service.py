from datetime import datetime

from sqlalchemy import func, or_

from core.extensions import db
from core.helpers import s, so_digits
from core.models import Cliente, Ligacao
from routes.clientes_ligacoes.cache_invalidation import invalidar_caches_listagens_clientes
from services.sse_bus import publicar_ligacao_registrada
from routes.clientes_ligacoes.ligacao_helpers import (
    calcular_proxima_ligacao,
    mensagem_sucesso_ligacao,
    normalizar_resultado_ligacao,
    parse_valor_venda,
)
from routes.clientes_ligacoes.permission_helpers import consultor_sem_permissao_no_cliente


def localizar_cliente_para_registro(cd_cliente_oracle_raw=None, cnpj_raw=None):
    cd_cliente_oracle = so_digits(cd_cliente_oracle_raw)
    cnpj_digits = so_digits(cnpj_raw)

    filtros = []
    if cd_cliente_oracle:
        filtros.append(Cliente.cd_cliente_oracle == cd_cliente_oracle)
    if cnpj_digits:
        cnpj_limpo = func.replace(
            func.replace(
                func.replace(
                    func.replace(func.coalesce(Cliente.cnpj, ""), ".", ""),
                    "/",
                    "",
                ),
                "-",
                "",
            ),
            " ",
            "",
        )
        filtros.append(cnpj_limpo == cnpj_digits)

    if not filtros:
        return None

    return (
        Cliente.query
        .filter(or_(*filtros))
        .order_by(Cliente.ativo.desc(), Cliente.id.desc())
        .first()
    )


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

    if current_user.tipo == "consultor" and cli.consultor_id != current_user.id:
        cli.consultor_id = current_user.id

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
    invalidar_caches_listagens_clientes("registro de ligacao")
    try:
        from routes.clientes_ligacoes.client_metrics import invalidar_cache_stats_locks
        invalidar_cache_stats_locks()
    except Exception:
        pass
    try:
        publicar_ligacao_registrada(
            cliente_id=cliente_id,
            consultor_nome=str(getattr(current_user, "nome", "") or ""),
            resultado=resultado,
        )
    except Exception:
        pass

    msg = mensagem_sucesso_ligacao(resultado, cli.proxima_ligacao)
    return {
        "ok": True,
        "mensagem": msg,
        "proxima_ligacao": cli.proxima_ligacao.isoformat() if cli.proxima_ligacao else None,
    }, 200
