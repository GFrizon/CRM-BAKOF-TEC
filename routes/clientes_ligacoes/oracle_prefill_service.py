from core.helpers import so_digits
from routes.clientes_ligacoes.oracle_sync_helpers import (
    montar_payload_cliente_oracle,
    sugerir_consultor_por_categoria_oracle,
)


def buscar_dados_oracle_para_preenchimento(cnpj_raw):
    cnpj = so_digits(cnpj_raw)
    if not cnpj or len(cnpj) < 7:
        return {"ok": False, "mensagem": "Informe um CNPJ valido (minimo 7 digitos)"}, 400

    from oracle_service import get_cliente_oracle_por_cnpj

    cliente_oracle = get_cliente_oracle_por_cnpj(cnpj)
    if not cliente_oracle:
        return {
            "ok": True,
            "encontrado": False,
            "mensagem": "CNPJ nao encontrado no Oracle",
        }, 200

    consultor_sugerido = sugerir_consultor_por_categoria_oracle(cliente_oracle.get("consultor"))
    return {
        "ok": True,
        "encontrado": True,
        "dados": montar_payload_cliente_oracle(cnpj, cliente_oracle, consultor_sugerido),
    }, 200
