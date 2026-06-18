from datetime import datetime, timedelta

from services.inativos_snapshot_service import (
    carregar_snapshot_inativos_oracle,
    montar_mapa_snapshot_inativos,
)


def _cliente_pertence_lista_publica_inativos(cliente):
    if not cliente:
        return False

    cd_cliente = str(getattr(cliente, "cd_cliente_oracle", "") or "").strip()
    ultimo_pedido = getattr(cliente, "ultimo_pedido_oracle", None)
    if not cd_cliente or not ultimo_pedido or getattr(cliente, "ativo", True) is not True:
        return False

    limite_max = datetime.now() - timedelta(days=181)
    limite_min = datetime.now() - timedelta(days=1095)
    if not (limite_min <= ultimo_pedido <= limite_max):
        return False

    snapshot = carregar_snapshot_inativos_oracle()
    if not snapshot:
        return True

    codigos_snapshot, _ = montar_mapa_snapshot_inativos(snapshot)
    return cd_cliente in codigos_snapshot


def consultor_sem_permissao_no_cliente(current_user, cliente):
    if current_user.tipo != "consultor":
        return False
    if cliente.consultor_id == current_user.id:
        return False
    if _cliente_pertence_lista_publica_inativos(cliente):
        return False
    return True


def consultor_sem_permissao_na_ligacao(current_user, ligacao):
    return current_user.tipo == "consultor" and ligacao.consultor_id != current_user.id
