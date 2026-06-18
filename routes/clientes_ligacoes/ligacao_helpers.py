from datetime import datetime, timedelta
import re


RESULTADOS_VALIDOS_LIGACAO = {
    "comprou",
    "nao_comprou",
    "retornar",
    "sem_interesse",
    "relacionamento",
    "cliente_inativo",
}


def parse_valor_venda(valor_raw):
    try:
        txt = str(valor_raw or "").strip()
        if not txt:
            return 0.0

        # Aceita formatos como:
        # 1234.56 | 1.234,56 | 1,234.56 | R$ 1.234,56
        txt = re.sub(r"[^\d,.\-]", "", txt)
        if not txt or txt in {"-", ".", ",", "-.", "-,"}:
            return 0.0

        tem_virgula = "," in txt
        tem_ponto = "." in txt

        if tem_virgula and tem_ponto:
            ultima_virgula = txt.rfind(",")
            ultimo_ponto = txt.rfind(".")
            separador_decimal = "," if ultima_virgula > ultimo_ponto else "."
            separador_milhar = "." if separador_decimal == "," else ","
            txt = txt.replace(separador_milhar, "")
            if separador_decimal == ",":
                txt = txt.replace(",", ".")
        elif tem_virgula:
            txt = txt.replace(".", "")
            txt = txt.replace(",", ".")
        else:
            txt = txt.replace(",", "")

        return float(txt)
    except Exception:
        return 0.0


def normalizar_resultado_ligacao(resultado_raw):
    resultado = str(resultado_raw or "nao_comprou").strip() or "nao_comprou"
    if resultado not in RESULTADOS_VALIDOS_LIGACAO:
        return "nao_comprou"
    return resultado


def calcular_proxima_ligacao(agora, resultado, data_retorno_raw, dias_retorno_raw):
    dias_retorno = None
    try:
        dias_retorno = int(dias_retorno_raw) if dias_retorno_raw else None
    except Exception:
        dias_retorno = None

    if data_retorno_raw:
        try:
            d = datetime.strptime(str(data_retorno_raw), "%Y-%m-%d").date()
            return datetime(d.year, d.month, d.day, 9, 0, 0)
        except Exception:
            return agora + timedelta(days=30)
    if dias_retorno and dias_retorno > 0:
        return agora + timedelta(days=dias_retorno)
    if resultado == "retornar":
        return agora + timedelta(days=30)
    return None


def mensagem_sucesso_ligacao(resultado, proxima_ligacao):
    if proxima_ligacao:
        return "Ligação registrada! Cliente marcado para retorno."
    if resultado == "comprou":
        return "Ligação registrada! Venda marcada como 'comprou'."
    return "Ligação registrada!"


def aplicar_payload_edicao_ligacao(ligacao, payload, normalizador_texto):
    # Editar resultado
    if "resultado" in payload:
        novo_resultado = normalizador_texto(payload.get("resultado"))
        if novo_resultado in RESULTADOS_VALIDOS_LIGACAO:
            ligacao.resultado = novo_resultado

    # Editar valor da venda
    if "valor_venda" in payload:
        ligacao.valor_venda = parse_valor_venda(payload.get("valor_venda"))

    # Editar observação
    if "observacao" in payload:
        ligacao.observacao = normalizador_texto(payload.get("observacao")) or None
