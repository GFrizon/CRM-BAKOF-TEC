from __future__ import annotations

from datetime import datetime
from typing import Any

from oracle_service import get_pedidos_em_andamento_recentes_oracle


def _normalizar_cd(valor: Any) -> str:
    txt = str(valor or "").strip()
    if not txt:
        return ""
    dig = "".join(ch for ch in txt if ch.isdigit())
    if dig:
        try:
            return str(int(dig))
        except Exception:
            return dig
    return txt.upper()


def marcar_pedido_em_andamento_payloads(
    payloads: list[dict[str, Any]],
    *,
    dias_recencia: int = 45,
) -> None:
    codigos = []
    vistos = set()
    for payload in payloads or []:
        cd = str(payload.get("cd_cliente_oracle") or "").strip()
        if not cd or cd in vistos:
            continue
        vistos.add(cd)
        codigos.append(cd)

    if not codigos:
        return

    mapa = get_pedidos_em_andamento_recentes_oracle(codigos, dias_recencia=dias_recencia) or {}
    if not mapa:
        return

    for payload in payloads:
        cd = str(payload.get("cd_cliente_oracle") or "").strip()
        if not cd:
            continue
        pedido = mapa.get(cd) or mapa.get(_normalizar_cd(cd))
        if not pedido:
            continue

        dt_pendente = pedido.get("dt_pedido")
        dt_referencia = payload.get("ultimo_pedido_oracle")
        if isinstance(dt_referencia, str):
            try:
                dt_referencia = datetime.fromisoformat(dt_referencia[:19])
            except Exception:
                dt_referencia = None

        if dt_pendente and dt_referencia and dt_pendente <= dt_referencia:
            continue

        payload["pedido_em_andamento"] = True
        payload["pedido_em_andamento_dt"] = dt_pendente
        payload["pedido_em_andamento_cd_pedido"] = pedido.get("cd_pedido")
        payload["pedido_em_andamento_status"] = str(pedido.get("situacao") or "").strip()
        payload["pedido_em_andamento_controle"] = str(pedido.get("controle") or "").strip()
        payload["pedido_em_andamento_desc_controle"] = str(pedido.get("desc_controle") or "").strip()
