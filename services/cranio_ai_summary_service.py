from __future__ import annotations

import json
import os
import re
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app

from services.cranio_ai_summary_snapshot_service import salvar_snapshot_cranio_ai_summary
from services.cranio_insights_snapshot_service import (
    carregar_snapshot_cranio_insights,
    carregar_snapshot_cranio_insights_na_data_ou_anterior,
)


def _gemini_api_keys():
    keys = []
    raw_multi = os.getenv("GEMINI_API_KEYS", "")
    if raw_multi:
        for part in re.split(r"[,;\n\r]+", raw_multi):
            k = str(part or "").strip()
            if k and k not in keys:
                keys.append(k)
    single = os.getenv("GEMINI_API_KEY", "").strip()
    if single and single not in keys:
        keys.append(single)
    return keys


def _chamar_gemini_json(prompt: str):
    api_keys = _gemini_api_keys()
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"
    if not api_keys:
        return None, "SEM_CHAVE"

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 900,
            "responseMimeType": "application/json",
        },
    }

    erros = []
    for idx, api_key in enumerate(api_keys, start=1):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = Request(
            url=url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            candidatos = data.get("candidates") or []
            if not candidatos:
                erros.append("SEM_RESPOSTA")
                continue
            partes = ((candidatos[0].get("content") or {}).get("parts") or [])
            textos = [p.get("text", "") for p in partes if isinstance(p, dict) and p.get("text")]
            bruto = "\n".join(textos).strip()
            if not bruto:
                erros.append("SEM_TEXTO")
                continue
            try:
                return json.loads(bruto), None
            except Exception:
                return {"resumo_executivo": bruto}, None
        except HTTPError as e:
            code = getattr(e, "code", 0) or 0
            try:
                body_err = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                body_err = ""
            current_app.logger.warning("[Cranio/IA] Gemini chave #%s HTTP %s: %s", idx, code, body_err)
            erros.append(f"HTTP_{code}")
        except URLError as e:
            current_app.logger.warning("[Cranio/IA] Gemini URLError: %s", e)
            erros.append("REDE")
        except Exception as e:
            current_app.logger.warning("[Cranio/IA] Gemini erro inesperado: %s", e)
            erros.append("ERRO")
    return None, (erros[-1] if erros else "SEM_RESPOSTA")


def _chamar_gemini_text(prompt: str):
    api_keys = _gemini_api_keys()
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"
    if not api_keys:
        return None, "SEM_CHAVE"

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 220,
        },
    }

    erros = []
    for idx, api_key in enumerate(api_keys, start=1):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = Request(
            url=url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            candidatos = data.get("candidates") or []
            if not candidatos:
                erros.append("SEM_RESPOSTA")
                continue
            partes = ((candidatos[0].get("content") or {}).get("parts") or [])
            textos = [p.get("text", "") for p in partes if isinstance(p, dict) and p.get("text")]
            bruto = "\n".join(textos).strip()
            if bruto:
                return bruto, None
            erros.append("SEM_TEXTO")
        except HTTPError as e:
            code = getattr(e, "code", 0) or 0
            try:
                body_err = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                body_err = ""
            current_app.logger.warning("[Cranio/IA] Gemini texto chave #%s HTTP %s: %s", idx, code, body_err)
            erros.append(f"HTTP_{code}")
        except URLError as e:
            current_app.logger.warning("[Cranio/IA] Gemini texto URLError: %s", e)
            erros.append("REDE")
        except Exception as e:
            current_app.logger.warning("[Cranio/IA] Gemini texto erro inesperado: %s", e)
            erros.append("ERRO")
    return None, (erros[-1] if erros else "SEM_RESPOSTA")


def _normalizar_conteudo_ia(conteudo):
    if isinstance(conteudo, dict):
        resumo = conteudo.get("resumo_executivo")
        if isinstance(resumo, str):
            resumo_txt = resumo.strip()
            if resumo_txt.startswith("{"):
                try:
                    parsed = json.loads(resumo_txt)
                    if isinstance(parsed, dict):
                        merged = dict(conteudo)
                        merged.update(parsed)
                        return merged
                except Exception:
                    match = re.search(r'"resumo_executivo"\s*:\s*"([^"]+)', resumo_txt, flags=re.S)
                    if match:
                        merged = dict(conteudo)
                        merged["resumo_executivo"] = match.group(1).strip()
                        return merged
        return conteudo

    if isinstance(conteudo, str):
        bruto = conteudo.strip()
        if bruto.startswith("{"):
            try:
                parsed = json.loads(bruto)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                match = re.search(r'"resumo_executivo"\s*:\s*"([^"]+)', bruto, flags=re.S)
                if match:
                    return {"resumo_executivo": match.group(1).strip()}
        return {"resumo_executivo": bruto}

    return {"resumo_executivo": str(conteudo or "").strip()}


def _montar_prompt_estrategico(visao: str, periodo: str, payload_visao: dict) -> str:
    metricas = dict((payload_visao or {}).get("metricas") or {})
    temas = list((payload_visao or {}).get("temas_principais") or [])[:6]
    alertas = list((payload_visao or {}).get("alertas") or [])[:4]
    oportunidades = list((payload_visao or {}).get("oportunidades") or [])[:4]
    operadores = list((payload_visao or {}).get("operadores") or [])[:6]

    material = {
        "visao": visao,
        "periodo": periodo,
        "periodo_label": (payload_visao or {}).get("periodo_label"),
        "metricas": metricas,
        "temas_principais": temas,
        "alertas_locais": alertas,
        "oportunidades_locais": oportunidades,
        "operadores": operadores,
    }

    return (
        "Voce e o Cranio Estrategico, analista comercial de um CRM.\n"
        "Analise os dados resumidos abaixo e responda em portugues-BR.\n"
        "Nao invente fatos, nao traga numeros fora do material recebido.\n"
        "Priorize leitura gerencial e comercial.\n"
        "Retorne JSON valido com as chaves:\n"
        "resumo_executivo, riscos, oportunidades, recomendacoes, sinais_por_operador.\n"
        "Cada uma deve ser objetiva e curta.\n"
        "Use no maximo:\n"
        "- resumo_executivo: 2 frases\n"
        "- riscos: 3 itens\n"
        "- oportunidades: 3 itens\n"
        "- recomendacoes: 3 itens\n"
        "Em sinais_por_operador, traga no maximo 4 itens em formato de lista de objetos com nome e leitura.\n\n"
        f"MATERIAL:\n{json.dumps(material, ensure_ascii=False, indent=2)}"
    )


def _montar_prompt_resumo_texto(visao: str, periodo: str, payload_visao: dict) -> str:
    material = {
        "visao": visao,
        "periodo": periodo,
        "periodo_label": (payload_visao or {}).get("periodo_label"),
        "metricas": dict((payload_visao or {}).get("metricas") or {}),
        "temas_principais": list((payload_visao or {}).get("temas_principais") or [])[:5],
        "alertas_locais": list((payload_visao or {}).get("alertas") or [])[:3],
        "oportunidades_locais": list((payload_visao or {}).get("oportunidades") or [])[:3],
        "operadores": list((payload_visao or {}).get("operadores") or [])[:4],
    }
    return (
        "Voce e o Cranio Estrategico, analista comercial de um CRM.\n"
        "Escreva apenas um resumo executivo curto, em portugues-BR, com 2 frases completas.\n"
        "Nao invente fatos e nao use markdown.\n"
        "O texto precisa terminar completo, sem cortar no meio.\n\n"
        f"MATERIAL:\n{json.dumps(material, ensure_ascii=False, indent=2)}"
    )


def _parece_resumo_incompleto(texto: str) -> bool:
    base = str(texto or "").strip()
    if len(base) < 90:
        return True
    finais_ruins = (",", ";", ":", " e", " mas", " porém", " porem")
    low = base.lower()
    return any(low.endswith(item) for item in finais_ruins)


def _montar_resumo_local_estrategico(visao: str, payload_visao: dict) -> str:
    metricas = dict((payload_visao or {}).get("metricas") or {})
    temas = list((payload_visao or {}).get("temas_principais") or [])[:3]
    operadores = list((payload_visao or {}).get("operadores") or [])[:2]
    visao_txt = "consultores" if visao == "consultores" else "televendas"
    lig = int(metricas.get("ligacoes") or 0)
    vend = int(metricas.get("vendas") or 0)
    ret = int(metricas.get("retornos") or 0)
    conv = round((vend / lig) * 100, 1) if lig else 0.0
    temas_txt = ", ".join(str(t.get("tema") or "") for t in temas if t.get("tema")) or "sem tema dominante claro"
    partes = [
        f"Na visão de {visao_txt}, o período registrou {lig} ligações, {vend} vendas e {ret} retornos, com conversão de {conv:.1f}%.",
        f"Os sinais mais recorrentes foram {temas_txt}.",
    ]
    if operadores:
        op = operadores[0]
        partes.append(
            f"{op.get('nome') or 'Um operador'} puxou o maior volume, com {int(op.get('ligacoes') or 0)} interações e tema principal em {op.get('tema_principal') or 'sem destaque'}."
        )
    return " ".join(partes)


def gerar_resumo_estrategico_ia(*, visao: str, periodo: str = "hoje", snapshot_payload: dict | None = None):
    if not current_app.config.get("CRANIO_AI_SUMMARY_ENABLED"):
        return {
            "ok": False,
            "motivo": "desabilitado",
            "mensagem": "Resumo estratégico com IA desabilitado por configuração.",
        }

    snapshot = snapshot_payload or carregar_snapshot_cranio_insights() or carregar_snapshot_cranio_insights_na_data_ou_anterior()
    if not snapshot:
        return {
            "ok": False,
            "motivo": "sem_snapshot",
            "mensagem": "Snapshot base do Crânio ainda não disponível.",
        }

    payload_visao = (((snapshot or {}).get("visoes") or {}).get(visao) or {}).get(periodo) or {}
    if not payload_visao:
        return {
            "ok": False,
            "motivo": "sem_dados",
            "mensagem": "Não há material consolidado suficiente para esta visão/período.",
        }

    prompt = _montar_prompt_estrategico(visao, periodo, payload_visao)
    resposta, erro = _chamar_gemini_json(prompt)
    if erro or not resposta:
        return {
            "ok": False,
            "motivo": erro or "falha_ia",
            "mensagem": "Falha ao gerar resumo estratégico com IA.",
        }

    conteudo = _normalizar_conteudo_ia(resposta)
    resumo_exec = str((conteudo or {}).get("resumo_executivo") or "").strip()
    if _parece_resumo_incompleto(resumo_exec):
        prompt_texto = _montar_prompt_resumo_texto(visao, periodo, payload_visao)
        texto_limpo, erro_texto = _chamar_gemini_text(prompt_texto)
        if texto_limpo and not erro_texto and not _parece_resumo_incompleto(texto_limpo):
            conteudo["resumo_executivo"] = texto_limpo.strip()
        else:
            conteudo["resumo_executivo"] = _montar_resumo_local_estrategico(visao, payload_visao)

    resumo = {
        "ok": True,
        "visao": visao,
        "periodo": periodo,
        "data_ref_base": (snapshot or {}).get("data_ref"),
        "gerado_em": datetime.now().isoformat(),
        "modelo": os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash",
        "conteudo": conteudo,
    }
    return resumo


def gerar_e_salvar_resumos_ia_diarios(*, visoes=None, periodo: str = "hoje"):
    visoes = list(visoes or ["consultores", "televendas"])
    snapshot = carregar_snapshot_cranio_insights() or carregar_snapshot_cranio_insights_na_data_ou_anterior()
    payload = {
        "data_ref": (snapshot or {}).get("data_ref"),
        "gerado_em": datetime.now().isoformat(),
        "resumos": {},
    }
    for visao in visoes:
        payload["resumos"][visao] = {}
        resultado = gerar_resumo_estrategico_ia(
            visao=visao,
            periodo=periodo,
            snapshot_payload=snapshot,
        )
        payload["resumos"][visao][periodo] = resultado
    salvar_snapshot_cranio_ai_summary(payload)
    return payload
