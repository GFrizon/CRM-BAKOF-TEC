from core.extensions import db
from core.models import Cliente, Usuario


def extrair_cds_da_requisicao(request):
    cds = []
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        for item in (payload.get("cds") or []):
            cd = str(item or "").strip()
            if cd:
                cds.append(cd)
    else:
        cds_raw = request.args.get("cds") or ""
        for item in cds_raw.split(","):
            cd = str(item or "").strip()
            if cd:
                cds.append(cd)
    # Remove duplicados mantendo ordem.
    return list(dict.fromkeys(cds))


def buscar_locks_por_cd_oracle(cds):
    if not cds:
        return {}

    rows = (
        db.session.query(
            Cliente.cd_cliente_oracle.label("cd_cliente_oracle"),
            Cliente.em_atendimento_por.label("em_atendimento_por"),
            Usuario.nome.label("usuario_nome"),
        )
        .outerjoin(Usuario, Usuario.id == Cliente.em_atendimento_por)
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.in_(cds),
            Cliente.em_atendimento_por.isnot(None),
        )
        .all()
    )

    locks = {}
    for row in rows:
        cd = str(row.cd_cliente_oracle or "").strip()
        if not cd:
            continue
        if cd not in locks:
            locks[cd] = {
                "ativo": True,
                "por_nome": (row.usuario_nome or "Outro usuario"),
                "ate": None,
            }
    return locks


def tentar_assumir_lock_cliente(cli, current_user_id, aba_contexto, cd_oracle_payload, forcar=False):
    usa_lock_compartilhado_inativos = (aba_contexto == "inativos")

    if usa_lock_compartilhado_inativos:
        cd_oracle_lock = str(cli.cd_cliente_oracle or cd_oracle_payload or "").strip()
        clientes_relacionados = []
        if cd_oracle_lock:
            clientes_relacionados = (
                Cliente.query
                .filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle == cd_oracle_lock,
                )
                .all()
            )
        if not clientes_relacionados:
            clientes_relacionados = [cli]

        bloqueado_por = None
        for cli_rel in clientes_relacionados:
            if cli_rel.em_atendimento_por and cli_rel.em_atendimento_por != current_user_id:
                bloqueado_por = cli_rel
                break

        if bloqueado_por and not forcar:
            usuario_lock = db.session.get(Usuario, bloqueado_por.em_atendimento_por)
            return False, {
                "ok": False,
                "bloqueado": True,
                "em_atendimento_por_id": bloqueado_por.em_atendimento_por,
                "em_atendimento_por_nome": (usuario_lock.nome if usuario_lock else "Outro usurio"),
                "em_atendimento_ate": None,
                "mensagem": "Cliente em atendimento por outro usurio.",
            }

        for cli_rel in clientes_relacionados:
            cli_rel.em_atendimento_por = current_user_id
            cli_rel.em_atendimento_ate = None
        cli.em_atendimento_por = current_user_id
        cli.em_atendimento_ate = None
        return True, None

    bloqueio_ativo = (
        cli.em_atendimento_por
        and cli.em_atendimento_por != current_user_id
    )
    if bloqueio_ativo and not forcar:
        usuario_lock = db.session.get(Usuario, cli.em_atendimento_por)
        return False, {
            "ok": False,
            "bloqueado": True,
            "em_atendimento_por_id": cli.em_atendimento_por,
            "em_atendimento_por_nome": (usuario_lock.nome if usuario_lock else "Outro usurio"),
            "em_atendimento_ate": None,
            "mensagem": "Cliente em atendimento por outro usurio.",
        }

    cli.em_atendimento_por = current_user_id
    cli.em_atendimento_ate = None
    return True, None
