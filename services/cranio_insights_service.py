from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
import re
import unicodedata

from core.models import Ligacao, Usuario


KEYWORD_GROUPS = {
    "preco": ("preco", "valor", "caro", "desconto", "condicao", "negoci"),
    "prazo": ("prazo", "entrega", "frete", "demora", "urgencia"),
    "estoque": ("estoque", "falta", "indisponivel", "ruptura"),
    "concorrencia": ("concorr", "outra loja", "outro fornecedor", "cotacao"),
    "retorno": ("retorno", "retornar", "depois", "amanha", "semana", "agenda"),
    "contato": ("nao atende", "nao respondeu", "whats", "telefone", "ocupado"),
    "interesse": ("interesse", "orcamento", "cotacao", "pedido", "fechar"),
}


def _normalize(texto: str) -> str:
    txt = str(texto or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
    txt = re.sub(r"[^a-z0-9\s]", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def _escopo_individual(current_user) -> bool:
    return getattr(current_user, "tipo", "") in ("consultor", "televendas")


def _base_ligacoes(*, inicio: datetime, fim: datetime, tipos_usuario=None, user_id=None):
    q = Ligacao.query.join(Usuario, Usuario.id == Ligacao.consultor_id)
    q = q.filter(
        Ligacao.data_hora >= inicio,
        Ligacao.data_hora < fim,
        Usuario.ativo == True,
    )
    if tipos_usuario:
        q = q.filter(Usuario.tipo.in_(list(tipos_usuario)))
    if user_id:
        q = q.filter(Ligacao.consultor_id == int(user_id))
    return q


def _rotulo_periodo(periodo: str) -> tuple[datetime, datetime, str]:
    agora = datetime.now()
    hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    periodo = str(periodo or "hoje").strip().lower()
    if periodo == "3d":
        return hoje - timedelta(days=2), agora, "Ultimos 3 dias"
    if periodo == "mes":
        inicio = hoje.replace(day=1)
        return inicio, agora, "Este mes"
    return hoje, agora, "Hoje"


def _extrair_temas(observacoes: list[str]) -> list[dict]:
    contador = Counter()
    exemplos = {}
    for obs in observacoes:
        base = _normalize(obs)
        if not base:
            continue
        for tema, termos in KEYWORD_GROUPS.items():
            if any(t in base for t in termos):
                contador[tema] += 1
                exemplos.setdefault(tema, obs[:140])
    return [
        {"tema": tema, "qtd": qtd, "exemplo": exemplos.get(tema, "")}
        for tema, qtd in contador.most_common(6)
    ]


def _resumo_executivo(*, total_ligacoes: int, total_vendas: int, total_retornos: int, temas: list[dict], visao_label: str, periodo_label: str) -> str:
    if total_ligacoes == 0:
        return f"Nao houve ligacoes registradas em {periodo_label.lower()} para a visao de {visao_label.lower()}."

    tema_txt = ", ".join(t["tema"] for t in temas[:3]) if temas else "sem tema dominante claro"
    taxa = round((total_vendas / total_ligacoes) * 100, 1) if total_ligacoes else 0.0
    return (
        f"Em {periodo_label.lower()}, a visao de {visao_label.lower()} registrou {total_ligacoes} interacoes, "
        f"{total_vendas} vendas e {total_retornos} retornos. Os assuntos mais recorrentes foram {tema_txt}. "
        f"A conversao observada no periodo ficou em {taxa:.1f}%."
    )


def _montar_alertas(*, temas: list[dict], total_retornos: int, operadores: list[dict]) -> list[str]:
    alertas = []
    if temas:
        topo = temas[0]
        if topo["qtd"] >= 5:
            alertas.append(f"O tema '{topo['tema']}' apareceu {topo['qtd']} vezes nas observacoes.")
    if total_retornos >= 5:
        alertas.append(f"Foram registrados {total_retornos} contatos marcados como retornar.")
    for op in operadores[:3]:
        if op["ligacoes"] >= 8 and op["vendas"] == 0:
            alertas.append(f"{op['nome']} teve volume alto sem vendas no periodo.")
            break
    return alertas[:4]


def _montar_oportunidades(*, temas: list[dict], operadores: list[dict]) -> list[str]:
    oportunidades = []
    temas_idx = {t["tema"]: t["qtd"] for t in temas}
    if temas_idx.get("retorno", 0) >= 3:
        oportunidades.append("Ha massa critica de retornos prometidos que pode virar agenda priorizada.")
    if temas_idx.get("interesse", 0) >= 3:
        oportunidades.append("Observacoes com intencao comercial merecem revisita rapida com proposta objetiva.")
    for op in operadores[:3]:
        if op["vendas"] >= 1 and op["ligacoes"] >= 1:
            oportunidades.append(f"{op['nome']} teve sinal de tracao e pode servir de referencia comercial.")
            break
    return oportunidades[:4]


def _gerar_insights_base(*, inicio: datetime, fim: datetime, periodo: str, periodo_label: str, visao_label: str, tipos_usuario=None, user_id=None) -> dict:
    q = _base_ligacoes(
        inicio=inicio,
        fim=fim,
        tipos_usuario=tipos_usuario,
        user_id=user_id,
    )
    rows = (
        q.with_entities(
            Ligacao.observacao,
            Ligacao.resultado,
            Ligacao.valor_venda,
            Ligacao.data_hora,
            Usuario.nome.label("operador_nome"),
            Usuario.tipo.label("operador_tipo"),
        )
        .order_by(Ligacao.data_hora.desc())
        .all()
    )

    total_ligacoes = len(rows)
    total_vendas = sum(1 for r in rows if r.resultado == "comprou")
    total_retornos = sum(1 for r in rows if r.resultado == "retornar")
    total_nao = sum(1 for r in rows if r.resultado == "nao_comprou")
    valor_total = round(sum(float(r.valor_venda or 0) for r in rows if r.resultado == "comprou"), 2)

    observacoes = [str(r.observacao or "").strip() for r in rows if str(r.observacao or "").strip()]
    temas = _extrair_temas(observacoes)

    por_operador = defaultdict(lambda: {"ligacoes": 0, "vendas": 0, "retornos": 0, "temas": Counter(), "tipo": ""})
    for r in rows:
        nome = str(r.operador_nome or "Operador sem nome")
        op = por_operador[nome]
        op["ligacoes"] += 1
        op["tipo"] = str(r.operador_tipo or "")
        if r.resultado == "comprou":
            op["vendas"] += 1
        if r.resultado == "retornar":
            op["retornos"] += 1
        base = _normalize(r.observacao or "")
        for tema, termos in KEYWORD_GROUPS.items():
            if base and any(t in base for t in termos):
                op["temas"][tema] += 1

    operadores = []
    for nome, dados in por_operador.items():
        tema_principal = dados["temas"].most_common(1)[0][0] if dados["temas"] else "sem destaque"
        operadores.append(
            {
                "nome": nome,
                "tipo": dados["tipo"],
                "ligacoes": int(dados["ligacoes"]),
                "vendas": int(dados["vendas"]),
                "retornos": int(dados["retornos"]),
                "tema_principal": tema_principal,
                "conversao": round((dados["vendas"] / dados["ligacoes"]) * 100, 1) if dados["ligacoes"] else 0.0,
            }
        )
    operadores.sort(key=lambda item: (-item["ligacoes"], -item["vendas"], item["nome"]))

    alertas = _montar_alertas(temas=temas, total_retornos=total_retornos, operadores=operadores)
    oportunidades = _montar_oportunidades(temas=temas, operadores=operadores)

    return {
        "periodo": periodo,
        "periodo_label": periodo_label,
        "visao_label": visao_label,
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "resumo_executivo": _resumo_executivo(
            total_ligacoes=total_ligacoes,
            total_vendas=total_vendas,
            total_retornos=total_retornos,
            temas=temas,
            visao_label=visao_label,
            periodo_label=periodo_label,
        ),
        "metricas": {
            "ligacoes": int(total_ligacoes),
            "vendas": int(total_vendas),
            "retornos": int(total_retornos),
            "nao_comprou": int(total_nao),
            "valor_total": float(valor_total),
        },
        "temas_principais": temas,
        "alertas": alertas,
        "oportunidades": oportunidades,
        "operadores": operadores[:6],
    }


def gerar_insights_por_visao(visao: str, periodo: str = "hoje") -> dict:
    inicio, fim, periodo_label = _rotulo_periodo(periodo)
    visao = str(visao or "").strip().lower()
    if visao == "televendas":
        return _gerar_insights_base(
            inicio=inicio,
            fim=fim,
            periodo=periodo,
            periodo_label=periodo_label,
            visao_label="Televendas",
            tipos_usuario=("televendas",),
        )
    return _gerar_insights_base(
        inicio=inicio,
        fim=fim,
        periodo=periodo,
        periodo_label=periodo_label,
        visao_label="Consultores",
        tipos_usuario=("consultor",),
    )


def gerar_insights_cranio(current_user, periodo: str = "hoje") -> dict:
    inicio, fim, periodo_label = _rotulo_periodo(periodo)
    if _escopo_individual(current_user):
        visao_label = "Minha carteira"
        return _gerar_insights_base(
            inicio=inicio,
            fim=fim,
            periodo=periodo,
            periodo_label=periodo_label,
            visao_label=visao_label,
            user_id=getattr(current_user, "id", None),
        )
    return _gerar_insights_base(
        inicio=inicio,
        fim=fim,
        periodo=periodo,
        periodo_label=periodo_label,
        visao_label="Equipe",
        tipos_usuario=("consultor", "televendas"),
    )
