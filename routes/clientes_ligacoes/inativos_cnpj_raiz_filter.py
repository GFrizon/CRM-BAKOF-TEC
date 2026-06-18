from __future__ import annotations

from core.models import CnpjRaizOcultoInativos


def extrair_cnpj_raiz(valor) -> str:
    digitos = "".join(ch for ch in str(valor or "") if ch.isdigit())
    if len(digitos) < 8:
        return ""
    return digitos[:8]


def normalizar_cnpjs_raiz_para_lista(valores) -> list[str]:
    vistos = set()
    cnpjs_raiz = []
    for valor in valores or []:
        raiz = extrair_cnpj_raiz(valor)
        if not raiz or raiz in vistos:
            continue
        vistos.add(raiz)
        cnpjs_raiz.append(raiz)
    return cnpjs_raiz


def carregar_cnpjs_raiz_ocultos_ativos() -> set[str]:
    return {
        str(row.cnpj_raiz or "").strip()
        for row in CnpjRaizOcultoInativos.query.filter_by(ativo=True).all()
        if str(row.cnpj_raiz or "").strip()
    }


def filtrar_clientes_inativos_por_cnpj_raiz_oculto(clientes_oracle_inativos, cnpjs_raiz_ocultos=None):
    if cnpjs_raiz_ocultos is None:
        cnpjs_raiz_ocultos = carregar_cnpjs_raiz_ocultos_ativos()
    if not cnpjs_raiz_ocultos:
        return list(clientes_oracle_inativos or [])

    return [
        cliente
        for cliente in (clientes_oracle_inativos or [])
        if extrair_cnpj_raiz((cliente or {}).get("cnpj")) not in cnpjs_raiz_ocultos
    ]
