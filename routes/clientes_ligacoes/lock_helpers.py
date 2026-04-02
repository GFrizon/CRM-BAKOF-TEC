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
