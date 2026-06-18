from datetime import datetime, timedelta

from core.extensions import db
from core.models import Cliente, Usuario
from services.sse_bus import publicar_lock_assumido, publicar_lock_liberado


LOCK_EM_ATENDIMENTO_HORAS = 24


def _agora():
    return datetime.now()


def _novo_vencimento_lock():
    return _agora() + timedelta(hours=LOCK_EM_ATENDIMENTO_HORAS)


def _lock_expirado(em_atendimento_ate):
    if not em_atendimento_ate:
        return True
    return em_atendimento_ate <= _agora()


def _serializar_ate(em_atendimento_ate):
    if not em_atendimento_ate:
        return None
    return em_atendimento_ate.strftime("%d/%m/%Y %H:%M")


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
            Cliente.em_atendimento_ate.label("em_atendimento_ate"),
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
        if _lock_expirado(row.em_atendimento_ate):
            continue
        if cd not in locks:
            locks[cd] = {
                "ativo": True,
                "por_nome": (row.usuario_nome or "Outro usuario"),
                "ate": _serializar_ate(row.em_atendimento_ate),
            }
    return locks


def tentar_assumir_lock_cliente(cli, current_user_id, aba_contexto, cd_oracle_payload, forcar=False):
    usa_lock_compartilhado_oracle = aba_contexto in ("inativos", "ativos", "construtoras")
    novo_vencimento = _novo_vencimento_lock()

    if usa_lock_compartilhado_oracle:
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
            if _lock_expirado(cli_rel.em_atendimento_ate):
                cli_rel.em_atendimento_por = None
                cli_rel.em_atendimento_ate = None
                continue
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
                "em_atendimento_ate": _serializar_ate(bloqueado_por.em_atendimento_ate),
                "mensagem": "Cliente em atendimento por outro usurio.",
            }

        for cli_rel in clientes_relacionados:
            cli_rel.em_atendimento_por = current_user_id
            cli_rel.em_atendimento_ate = novo_vencimento
        cli.em_atendimento_por = current_user_id
        cli.em_atendimento_ate = novo_vencimento
        try:
            usuario_obj = db.session.get(Usuario, current_user_id)
            publicar_lock_assumido(
                cd_cliente=str(cli.cd_cliente_oracle or ""),
                usuario_nome=str(getattr(usuario_obj, "nome", "") or ""),
            )
        except Exception:
            pass
        return True, None

    if _lock_expirado(cli.em_atendimento_ate):
        cli.em_atendimento_por = None
        cli.em_atendimento_ate = None

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
            "em_atendimento_ate": _serializar_ate(cli.em_atendimento_ate),
            "mensagem": "Cliente em atendimento por outro usurio.",
        }

    cli.em_atendimento_por = current_user_id
    cli.em_atendimento_ate = novo_vencimento
    try:
        usuario_obj = db.session.get(Usuario, current_user_id)
        publicar_lock_assumido(
            cd_cliente=str(cli.cd_cliente_oracle or ""),
            usuario_nome=str(getattr(usuario_obj, "nome", "") or ""),
        )
    except Exception:
        pass
    return True, None
