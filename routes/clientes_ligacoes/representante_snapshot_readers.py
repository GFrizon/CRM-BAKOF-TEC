from datetime import datetime, timedelta

from sqlalchemy.orm import load_only

from core.models import Cliente
from services.inativos_snapshot_service import (
    carregar_snapshot_inativos_oracle,
    montar_mapa_snapshot_inativos,
    snapshot_inativos_cobre_janela,
)
from services.ativos_snapshot_service import carregar_snapshot_ativos_oracle
from services.construtoras_snapshot_service import (
    carregar_snapshot_construtoras_oracle,
    rows_snapshot_construtoras,
)
from services.oracle_snapshot_service import (
    carregar_snapshot_oracle_90_150,
    rows_snapshot_oracle_90_150,
)


def _restaurar_dt(valor):
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor
    try:
        return datetime.fromisoformat(str(valor))
    except Exception:
        return valor


def carregar_clientes_oracle_snapshot_representante():
    snapshot = carregar_snapshot_oracle_90_150()
    if not snapshot or not isinstance(snapshot.get("itens"), list):
        return []
    return rows_snapshot_oracle_90_150(snapshot)


def carregar_clientes_ativos_snapshot_representante():
    snapshot = carregar_snapshot_ativos_oracle()
    itens = (snapshot or {}).get("itens") or []
    if not isinstance(itens, list):
        return []
    rows = []
    for item in itens:
        row = dict(item or {})
        row["dt_pedido"] = _restaurar_dt(row.get("dt_pedido"))
        rows.append(row)
    return rows


def carregar_clientes_construtoras_snapshot_representante():
    snapshot = carregar_snapshot_construtoras_oracle()
    if not snapshot or not isinstance(snapshot.get("itens"), list):
        return []
    return rows_snapshot_construtoras(snapshot)


def carregar_clientes_inativos_snapshot_representante():
    limite_max = datetime.now() - timedelta(days=181)
    limite_min = datetime.now() - timedelta(days=1095)
    clientes_inativos_local = (
        Cliente.query
        .options(
            load_only(
                Cliente.id,
                Cliente.nome,
                Cliente.cnpj,
                Cliente.telefone,
                Cliente.telefone2,
                Cliente.ativo,
                Cliente.cd_cliente_oracle,
                Cliente.categoria_consultor,
                Cliente.conceito,
                Cliente.ultimo_pedido_oracle,
                Cliente.valor_ultimo_pedido,
                Cliente.situacao_ultimo_pedido,
                Cliente.representante_oracle,
                Cliente.municipio,
                Cliente.uf,
                Cliente.contato,
                Cliente.data_ultima_sincronizacao,
            )
        )
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.between(limite_min, limite_max),
        )
        .all()
    )

    codigos_inativos_oracle = set()
    centralizadora_por_cd = {}
    snapshot = carregar_snapshot_inativos_oracle()
    if snapshot and snapshot_inativos_cobre_janela(snapshot):
        codigos_inativos_oracle, centralizadora_por_cd = montar_mapa_snapshot_inativos(snapshot)

    if codigos_inativos_oracle:
        clientes_inativos_local = [
            c for c in clientes_inativos_local
            if str(c.cd_cliente_oracle or "").strip() in codigos_inativos_oracle
        ]

    dedup_por_cd = {}
    for c in clientes_inativos_local:
        cd = str(c.cd_cliente_oracle or "").strip()
        if not cd:
            continue
        atual = dedup_por_cd.get(cd)
        if not atual:
            dedup_por_cd[cd] = c
            continue
        atual_pedido = atual.ultimo_pedido_oracle or datetime.min
        novo_pedido = c.ultimo_pedido_oracle or datetime.min
        atual_sync = atual.data_ultima_sincronizacao or datetime.min
        novo_sync = c.data_ultima_sincronizacao or datetime.min
        if (novo_pedido, novo_sync, int(c.id or 0)) > (atual_pedido, atual_sync, int(atual.id or 0)):
            dedup_por_cd[cd] = c

    return [
        {
            "cd_cliente": c.cd_cliente_oracle,
            "cliente": c.nome,
            "cnpj": c.cnpj,
            "telefone1": c.telefone,
            "telefone2": c.telefone2,
            "representante": c.representante_oracle,
            "consultor": c.categoria_consultor,
            "conceito": c.conceito,
            "municipio": c.municipio,
            "uf": c.uf,
            "contato": c.contato,
            "dt_pedido": c.ultimo_pedido_oracle,
            "total_pedido": c.valor_ultimo_pedido,
            "situacao": c.situacao_ultimo_pedido,
            "cd_centralizado": (
                centralizadora_por_cd.get(str(c.cd_cliente_oracle).strip(), {}).get("cd_centralizado")
                if c.cd_cliente_oracle else None
            ),
            "nome_centralizadora": (
                centralizadora_por_cd.get(str(c.cd_cliente_oracle).strip(), {}).get("nome_centralizadora")
                if c.cd_cliente_oracle else None
            ),
        }
        for c in dedup_por_cd.values()
    ]
