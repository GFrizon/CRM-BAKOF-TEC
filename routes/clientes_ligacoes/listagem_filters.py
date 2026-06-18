from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping, Sequence


def extrair_filtros_listagem(request: Any) -> tuple[str, str, str]:
    conceito_filtro = (request.args.get("conceito_filtro") or "").strip().upper()
    consultor_filtro = (request.args.get("consultor_filtro") or "").strip()
    termo = (request.args.get("q") or "").strip()
    return conceito_filtro, consultor_filtro, termo


def corresponde_conceito_filtro(conceito_filtro: str, conceito_cliente: str) -> bool:
    if not conceito_filtro:
        return True
    if conceito_filtro in ("SEM_CONCEITO", "SEM CONCEITO"):
        return conceito_cliente in ("", "SEM CONCEITO")
    return conceito_cliente == conceito_filtro


def corresponde_consultor_filtro(consultor_filtro: str, consultor_cliente: str) -> bool:
    if not consultor_filtro:
        return True
    return _normalizar_texto_busca(consultor_filtro) in _normalizar_texto_busca(consultor_cliente)


def _normalizar_texto_busca(valor: str) -> str:
    texto = str(valor or "").strip().lower()
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"\s+", " ", texto)
    return texto


def _somente_digitos(valor: str) -> str:
    return re.sub(r"\D+", "", str(valor or ""))


def corresponde_termo_busca(
    termo: str,
    registro: Mapping[str, Any],
    campos_busca: Sequence[str],
) -> bool:
    if not termo:
        return True
    termo_normalizado = _normalizar_texto_busca(termo)
    base_busca = " ".join(str(registro.get(campo) or "") for campo in campos_busca)
    base_normalizada = _normalizar_texto_busca(base_busca)
    if termo_normalizado and termo_normalizado in base_normalizada:
        return True

    # Permite buscar CNPJ/telefone/codigos mesmo com ou sem mascara/pontuacao.
    termo_digitos = _somente_digitos(termo)
    if termo_digitos:
        base_digitos = _somente_digitos(base_busca)
        if termo_digitos in base_digitos:
            return True

    return False
