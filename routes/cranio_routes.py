from datetime import datetime, timedelta
import json
import os
import random
import re
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import jsonify, render_template, request, session
from flask_login import current_user, login_required
from sqlalchemy import func

from core.models import Cliente, Ligacao, Usuario

_AI_USAGE_BY_DAY = {}


def register_cranio_routes(app):
    def _base_clientes_visiveis():
        q = Cliente.query.filter(Cliente.ativo == True)
        if current_user.tipo in ("consultor", "televendas"):
            q = q.filter(Cliente.consultor_id == current_user.id)
        return q

    def _normalize(texto: str) -> str:
        txt = str(texto or "").strip().lower()
        txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
        txt = re.sub(r"[^a-z0-9\s]", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    def _get_ctx():
        ctx = session.get("cranio_ctx")
        if isinstance(ctx, dict):
            return ctx
        return {
            "history": [],
            "pending_search": [],
            "last_intent": "",
            "interactions": 0,
            "good_interactions": 0,
            "bad_interactions": 0,
        }

    def _save_ctx(ctx: dict):
        history = list(ctx.get("history") or [])[-10:]
        pending = list(ctx.get("pending_search") or [])[:8]
        session["cranio_ctx"] = {
            "history": history,
            "pending_search": pending,
            "last_intent": str(ctx.get("last_intent") or ""),
            "interactions": int(ctx.get("interactions") or 0),
            "good_interactions": int(ctx.get("good_interactions") or 0),
            "bad_interactions": int(ctx.get("bad_interactions") or 0),
        }
        session.modified = True

    def _record_history(ctx: dict, pergunta: str, resposta: str, intent: str = ""):
        hist = list(ctx.get("history") or [])
        hist.append(
            {
                "q": str(pergunta or "")[:300],
                "a": str(resposta or "")[:700],
                "at": datetime.now().strftime("%d/%m %H:%M"),
            }
        )
        ctx["history"] = hist[-10:]
        ctx["interactions"] = int(ctx.get("interactions") or 0) + 1
        resposta_norm = _normalize(resposta or "")
        ruim = (
            ("nao entendi" in resposta_norm)
            or ("nao encontrei" in resposta_norm)
            or ("cochilada" in resposta_norm)
            or ("folga" in resposta_norm)
        )
        if intent and intent not in ("fallback_ia",):
            ctx["good_interactions"] = int(ctx.get("good_interactions") or 0) + 1
        if ruim:
            ctx["bad_interactions"] = int(ctx.get("bad_interactions") or 0) + 1
        if intent:
            ctx["last_intent"] = intent

    def _estado_cranio(ctx: dict):
        hoje = datetime.now().date()
        q_lig = Ligacao.query.filter(func.date(Ligacao.data_hora) == hoje)
        if current_user.tipo in ("consultor", "televendas"):
            q_lig = q_lig.filter(Ligacao.consultor_id == current_user.id)
        total_lig = q_lig.count()
        total_vendas = q_lig.filter(Ligacao.resultado == "comprou").count()
        total_perdidos = q_lig.filter(Ligacao.resultado == "nao_comprou").count()
        bad = int(ctx.get("bad_interactions") or 0)

        if total_vendas >= 3:
            humor = {"emoji": "👑", "nome": "lendário"}
        elif total_perdidos >= 3 and total_vendas == 0:
            humor = {"emoji": "💀", "nome": "pistola"}
        elif total_vendas >= 1:
            humor = {"emoji": "🔥", "nome": "animado"}
        elif bad >= 3:
            humor = {"emoji": "🙂", "nome": "tranquilo"}
        else:
            humor = {"emoji": "🧠", "nome": "normal"}

        score = (total_lig * 1) + (total_vendas * 6) + int(ctx.get("good_interactions") or 0) - (bad * 2)
        if score < 5:
            evolucao = {"emoji": "💀", "nome": "Crânio morto"}
        elif score < 18:
            evolucao = {"emoji": "🧠", "nome": "Crânio normal"}
        elif score < 40:
            evolucao = {"emoji": "🔥", "nome": "Crânio brabo"}
        else:
            evolucao = {"emoji": "👑", "nome": "Crânio lendário"}

        return {
            "humor": humor,
            "evolucao": evolucao,
            "metricas": {
                "ligacoes_hoje": total_lig,
                "vendas_hoje": total_vendas,
                "leads_perdidos_hoje": total_perdidos,
                "score": score,
            },
        }

    def _resumo_hoje():
        hoje = datetime.now().date()
        q_lig = Ligacao.query.filter(func.date(Ligacao.data_hora) == hoje)
        if current_user.tipo in ("consultor", "televendas"):
            q_lig = q_lig.filter(Ligacao.consultor_id == current_user.id)

        total_lig = q_lig.count()
        q_vendas = q_lig.filter(Ligacao.resultado == "comprou")
        total_vendas = q_vendas.count()
        total_retornar = q_lig.filter(Ligacao.resultado == "retornar").count()
        total_nao = q_lig.filter(Ligacao.resultado == "nao_comprou").count()
        val_result = q_vendas.with_entities(func.sum(Ligacao.valor_venda)).scalar()
        valor_vendas = float(val_result or 0)
        taxa = int(total_vendas / total_lig * 100) if total_lig > 0 else 0
        meta = current_user.meta_diaria or 0
        linhas = [
            f"Resumo de hoje ({hoje.strftime('%d/%m/%Y')}):",
            f"- Ligacoes: {total_lig}" + (f"/{meta} (meta)" if meta else ""),
            f"- Vendas: {total_vendas} | Conversao: {taxa}%" + (f" | Valor: R$ {valor_vendas:,.2f}" if valor_vendas else ""),
            f"- Retornar: {total_retornar} | Nao comprou: {total_nao}",
        ]
        return "\n".join(linhas)

    def _total_clientes():
        total = _base_clientes_visiveis().count()
        if current_user.tipo == "consultor":
            return f"Voce tem {total} clientes ativos na sua carteira."
        if current_user.tipo == "televendas":
            return f"Voce tem {total} clientes ativos na sua carteira de televendas."
        return f"Total de clientes ativos visiveis: {total}."

    def _periodo_mes_from_text(pergunta_norm: str):
        agora = datetime.now()
        ano = agora.year
        mes = agora.month

        if any(k in pergunta_norm for k in ("mes passado", "m s passado", "ultimo mes", "mes anterior")):
            if mes == 1:
                mes = 12
                ano -= 1
            else:
                mes -= 1
        elif any(k in pergunta_norm for k in ("este mes", "mes atual", "nesse mes", "neste mes")):
            pass
        else:
            m_num = re.search(r"\b(0?[1-9]|1[0-2])\s*/\s*(20\d{2})\b", pergunta_norm)
            if m_num:
                mes = int(m_num.group(1))
                ano = int(m_num.group(2))
            else:
                meses = {
                    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4,
                    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
                    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
                }
                mes_nome = None
                for nome, idx in meses.items():
                    if nome in pergunta_norm:
                        mes_nome = idx
                        break
                if mes_nome:
                    mes = mes_nome
                    ano_match = re.search(r"\b(20\d{2})\b", pergunta_norm)
                    if ano_match:
                        ano = int(ano_match.group(1))
                elif "mes" not in pergunta_norm:
                    return None

        inicio = datetime(ano, mes, 1)
        if mes == 12:
            fim = datetime(ano + 1, 1, 1)
        else:
            fim = datetime(ano, mes + 1, 1)
        label = f"{mes:02d}/{ano}"
        return inicio, fim, label

    def _quem_mais_ligou(pergunta_norm: str):
        periodo = _periodo_mes_from_text(pergunta_norm)
        q = Ligacao.query.with_entities(Ligacao.consultor_id, func.count(Ligacao.id).label("qtd"))
        label = "hoje"

        if periodo:
            inicio, fim, label = periodo
            q = q.filter(Ligacao.data_hora >= inicio, Ligacao.data_hora < fim)
        else:
            hoje = datetime.now().date()
            q = q.filter(func.date(Ligacao.data_hora) == hoje)

        if current_user.tipo in ("consultor", "televendas"):
            q = q.filter(Ligacao.consultor_id == current_user.id)

        top = q.group_by(Ligacao.consultor_id).order_by(func.count(Ligacao.id).desc()).limit(5).all()
        if not top:
            return f"Nao houve ligacoes registradas no periodo {label}."

        ids = [t[0] for t in top if t[0]]
        nomes = {u.id: u.nome for u in Usuario.query.filter(Usuario.id.in_(ids)).all()} if ids else {}
        linhas = [f"{nomes.get(cid, f'ID {cid}')}: {qtd} ligacoes" for cid, qtd in top]
        return f"No periodo {label}, quem mais ligou foi:\n- " + "\n- ".join(linhas)

    def _retornos_atrasados():
        agora = datetime.now()
        q = _base_clientes_visiveis().filter(
            Cliente.proxima_ligacao.isnot(None),
            Cliente.proxima_ligacao < agora,
        )
        qtd = q.count()
        if qtd == 0:
            return "Nao ha clientes com retorno atrasado no momento."
        nomes = [c.nome for c in q.order_by(Cliente.proxima_ligacao.asc()).limit(5).all()]
        return f"Voce tem {qtd} retornos atrasados. Priorize: {', '.join(nomes)}."

    def _proximos_ligar():
        q = _base_clientes_visiveis().filter(Cliente.proxima_ligacao.isnot(None))
        if current_user.tipo in ("consultor", "televendas"):
            q = q.filter(Cliente.consultor_id == current_user.id)
        itens = q.order_by(Cliente.proxima_ligacao.asc()).limit(5).all()
        if not itens:
            return "Nao encontrei proximos retornos agendados."
        lista = ", ".join(
            f"{c.nome} ({c.proxima_ligacao.strftime('%d/%m %H:%M')})" for c in itens if c.proxima_ligacao
        )
        return f"Proximos clientes para ligar: {lista}."

    def _q_clientes_inativam_no_mes(pergunta_norm: str):
        periodo = _periodo_mes_from_text(pergunta_norm)
        agora = datetime.now()
        if periodo:
            inicio_mes, fim_mes, label = periodo
        else:
            inicio_mes = datetime(agora.year, agora.month, 1)
            fim_mes = datetime(agora.year + (1 if agora.month == 12 else 0), 1 if agora.month == 12 else agora.month + 1, 1)
            label = f"{agora.month:02d}/{agora.year}"

        q = (
            _base_clientes_visiveis()
            .join(Usuario, Cliente.consultor_id == Usuario.id)
            .filter(
                Usuario.ativo == True,
                Cliente.ultimo_pedido_oracle.isnot(None),
            )
        )
        clientes = q.with_entities(Cliente.nome, Cliente.ultimo_pedido_oracle).all()
        if not clientes:
            return f"Nao encontrei clientes da carteira ativa para analisar inativacao em {label}."

        inativam = []
        for nome, dt_pedido in clientes:
            if not dt_pedido:
                continue
            dt_inativa = dt_pedido + timedelta(days=181)
            if inicio_mes <= dt_inativa < fim_mes:
                inativam.append((nome, dt_inativa))

        total = len(inativam)
        if total == 0:
            return f"Neste periodo ({label}), nenhum cliente da carteira ativa entra em inativacao."

        inativam.sort(key=lambda x: x[1])
        amostra = ", ".join([f"{n} ({d.strftime('%d/%m')})" for n, d in inativam[:5]])
        return (
            f"No periodo {label}, {total} cliente{'s' if total != 1 else ''} da carteira ativa entram em inativacao. "
            f"Exemplos: {amostra}."
        )

    def _montar_contexto_crm():
        agora = datetime.now()
        linhas = [
            f"Data/hora: {agora.strftime('%d/%m/%Y %H:%M')}",
            f"Usuario: {current_user.nome} | Perfil: {current_user.tipo} | Meta diaria: {current_user.meta_diaria} ligacoes",
        ]
        try:
            hoje = agora.date()
            q_h = Ligacao.query.filter(func.date(Ligacao.data_hora) == hoje)
            if current_user.tipo in ("consultor", "televendas"):
                q_h = q_h.filter(Ligacao.consultor_id == current_user.id)
            lig_hoje = q_h.count()
            vend_hoje = q_h.filter(Ligacao.resultado == "comprou").count()
            ret_hoje = q_h.filter(Ligacao.resultado == "retornar").count()
            nao_hoje = q_h.filter(Ligacao.resultado == "nao_comprou").count()
            val_r = q_h.filter(Ligacao.resultado == "comprou").with_entities(func.sum(Ligacao.valor_venda)).scalar()
            val_hoje = float(val_r or 0)
            taxa = int(vend_hoje / lig_hoje * 100) if lig_hoje else 0
            meta = current_user.meta_diaria or 0
            linhas.append(
                f"Ligacoes hoje: {lig_hoje}" + (f"/{meta}" if meta else "") +
                f" | Vendas: {vend_hoje} (R$ {val_hoje:,.2f}) | Conversao: {taxa}%"
                f" | Retornar: {ret_hoje} | Nao comprou: {nao_hoje}"
            )
        except Exception:
            pass
        try:
            total_cli = _base_clientes_visiveis().count()
            linhas.append(f"Clientes ativos visiveis: {total_cli}")
        except Exception:
            pass
        try:
            q_atr = _base_clientes_visiveis().filter(
                Cliente.proxima_ligacao.isnot(None),
                Cliente.proxima_ligacao < agora,
            )
            qtd_atr = q_atr.count()
            if qtd_atr:
                nms = [c.nome for c in q_atr.order_by(Cliente.proxima_ligacao.asc()).limit(3).all()]
                linhas.append(f"Retornos atrasados: {qtd_atr} cliente(s). Prioritarios: {', '.join(nms)}")
            else:
                linhas.append("Retornos atrasados: nenhum")
        except Exception:
            pass
        try:
            q_prx = _base_clientes_visiveis().filter(
                Cliente.proxima_ligacao.isnot(None),
                Cliente.proxima_ligacao >= agora,
            ).order_by(Cliente.proxima_ligacao.asc()).limit(3).all()
            if q_prx:
                prx = [f"{c.nome} ({c.proxima_ligacao.strftime('%d/%m %H:%M')})" for c in q_prx]
                linhas.append(f"Proximos agendados: {', '.join(prx)}")
        except Exception:
            pass
        try:
            linhas.append(_q_clientes_inativam_no_mes("este mes"))
        except Exception:
            pass
        return "\n".join(linhas)

    def _cliente_snapshot(cliente: Cliente):
        ult = cliente.ultimo_pedido_oracle.strftime("%d/%m/%Y") if cliente.ultimo_pedido_oracle else "-"
        prox = cliente.proxima_ligacao.strftime("%d/%m/%Y %H:%M") if cliente.proxima_ligacao else "-"
        consultor_nome = cliente.consultor.nome if cliente.consultor else "-"
        return (
            f"{cliente.nome} | CNPJ {cliente.cnpj or '-'} | Consultor {consultor_nome} | "
            f"Ult. pedido {ult} | Prox. ligacao {prox}"
        )

    def _selecionar_cliente_pendente(ctx: dict, pergunta_norm: str):
        pendentes = list(ctx.get("pending_search") or [])
        if not pendentes:
            return None

        numero = None
        m = re.match(r"^(?:n\s*)?(\d{1,2})$", pergunta_norm)
        if m:
            numero = int(m.group(1))

        escolhido = None
        if numero and 1 <= numero <= len(pendentes):
            escolhido = pendentes[numero - 1]
        else:
            for cand in pendentes:
                cnpj_digits = re.sub(r"\D", "", str(cand.get("cnpj") or ""))
                nome_norm = _normalize(cand.get("nome") or "")
                if cnpj_digits and cnpj_digits in pergunta_norm.replace(" ", ""):
                    escolhido = cand
                    break
                if nome_norm and nome_norm in pergunta_norm:
                    escolhido = cand
                    break

        if not escolhido:
            return None

        cliente = _base_clientes_visiveis().filter(Cliente.id == int(escolhido.get("id"))).first()
        ctx["pending_search"] = []
        if not cliente:
            return "Esse cliente nao esta mais visivel para seu perfil."
        return _cliente_snapshot(cliente)

    def _buscar_cliente(texto: str, ctx: dict):
        termo = str(texto or "").strip()
        if not termo:
            return "Diga o nome ou CNPJ para eu buscar cliente."

        q = _base_clientes_visiveis().filter(
            (Cliente.nome.ilike(f"%{termo}%")) | (Cliente.cnpj.ilike(f"%{termo}%"))
        )
        itens = q.order_by(Cliente.nome.asc()).limit(8).all()

        if not itens and len(termo) >= 4:
            termo_norm = _normalize(termo)
            candidatos = _base_clientes_visiveis().order_by(Cliente.nome.asc()).limit(350).all()
            itens = [
                c for c in candidatos
                if termo_norm in _normalize(c.nome or "") or termo_norm in re.sub(r"\D", "", c.cnpj or "")
            ][:8]

        if not itens:
            ctx["pending_search"] = []
            return f"Nao encontrei cliente para '{termo}'."

        if len(itens) == 1:
            ctx["pending_search"] = []
            return _cliente_snapshot(itens[0])

        ctx["pending_search"] = [
            {"id": c.id, "nome": c.nome or "", "cnpj": c.cnpj or ""} for c in itens
        ]
        linhas = [f"{i+1}) {c.nome} ({c.cnpj or '-'})" for i, c in enumerate(itens)]
        return "Encontrei mais de um cliente. Me diga o numero:\n- " + "\n- ".join(linhas)

    def _permitir_ia_hoje():
        dia = datetime.now().strftime("%Y-%m-%d")
        limite = int(os.getenv("CRANIO_AI_DAILY_LIMIT", "0"))
        if limite <= 0:
            return True, 0, 0
        user_key = f"{dia}:{current_user.id}"
        uso = int(_AI_USAGE_BY_DAY.get(user_key, 0))
        if uso >= limite:
            return False, uso, limite
        _AI_USAGE_BY_DAY[user_key] = uso + 1
        return True, uso + 1, limite

    def _chamar_gemini(pergunta: str, ctx: dict):
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"
        if not api_key:
            return None, "SEM_CHAVE"

        crm_ctx = _montar_contexto_crm()
        hist = list(ctx.get("history") or [])[-6:]
        hist_txt = (
            "\n".join([f"Usuario: {h.get('q','')}\nCranio: {h.get('a','')}" for h in hist])
            if hist else "Sem historico anterior."
        )
        prompt = (
            "Voce e o Cranio, assistente inteligente de um CRM de ligacoes comerciais.\n"
            "Responda SEMPRE em portugues-BR, de forma direta e util para a equipe de vendas.\n"
            "Use os dados do CRM abaixo para responder perguntas sobre resultados, clientes e equipe.\n"
            "Seja objetivo. Use listas apenas quando for listar varios itens.\n"
            "NUNCA invente numeros que nao estejam nos dados. Se o dado nao estiver disponivel, "
            "diga claramente e sugira um proximo passo.\n\n"
            f"=== DADOS ATUAIS DO CRM ===\n{crm_ctx}\n===========================\n\n"
            f"=== HISTORICO DA CONVERSA ===\n{hist_txt}\n=============================\n\n"
            f"Pergunta: {pergunta}"
        )

        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 600},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = Request(
            url=url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        def _do_gemini_request():
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            data = _do_gemini_request()
        except HTTPError as e:
            code = getattr(e, "code", 0) or 0
            try:
                body = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                body = ""
            print(f"[Cranio/Gemini] HTTP {code}: {body}")
            if code == 503:
                import time as _time
                _time.sleep(2)
                try:
                    data = _do_gemini_request()
                except Exception:
                    return None, "INDISPONIVEL"
            elif code == 429:
                return None, "QUOTA"
            elif code == 401:
                return None, "CHAVE_INVALIDA"
            elif code == 403:
                body_low = body.lower()
                if "1010" in body_low:
                    return None, "ACESSO_BLOQUEADO"
                return None, "PERMISSAO_NEGADA"
            else:
                return None, f"HTTP_{code}"
        except URLError as e:
            print(f"[Cranio/Gemini] URLError: {e}")
            return None, "REDE"
        except Exception as e:
            print(f"[Cranio/Gemini] Erro inesperado: {e}")
            return None, "ERRO"

        candidatos = data.get("candidates") or []
        if not candidatos:
            return None, "SEM_RESPOSTA"
        partes = ((candidatos[0].get("content") or {}).get("parts") or [])
        textos = [p.get("text", "") for p in partes if isinstance(p, dict) and p.get("text")]
        if not textos:
            return None, "SEM_TEXTO"
        return "\n".join(textos).strip(), None

    def _chamar_xai(pergunta: str, ctx: dict):
        api_key = os.getenv("XAI_API_KEY", "").strip()
        model = os.getenv("XAI_MODEL", "grok-3-mini").strip() or "grok-3-mini"
        if not api_key:
            return None, "SEM_CHAVE"

        crm_ctx = _montar_contexto_crm()
        hist = list(ctx.get("history") or [])[-6:]
        hist_txt = (
            "\n".join([f"Usuario: {h.get('q','')}\nCranio: {h.get('a','')}" for h in hist])
            if hist else "Sem historico anterior."
        )

        system_prompt = (
            "Voce e o Cranio, assistente inteligente de um CRM de ligacoes comerciais. "
            "Responda sempre em portugues-BR, com objetividade. "
            "Nao invente numeros; se faltar dado, diga claramente."
        )
        user_prompt = (
            f"=== DADOS ATUAIS DO CRM ===\n{crm_ctx}\n===========================\n\n"
            f"=== HISTORICO DA CONVERSA ===\n{hist_txt}\n=============================\n\n"
            f"Pergunta: {pergunta}"
        )

        body = {
            "model": model,
            "temperature": 0.3,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = Request(
            url="https://api.x.ai/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=14) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
        except HTTPError as e:
            code = getattr(e, "code", 0) or 0
            try:
                body = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                body = ""
            print(f"[Cranio/xAI] HTTP {code}: {body}")
            if code == 429:
                if "credits" in body.lower() or "spending limit" in body.lower():
                    return None, "CREDITOS"
                return None, "QUOTA"
            if code == 503:
                return None, "INDISPONIVEL"
            if code == 401:
                return None, "CHAVE_INVALIDA"
            if code == 403:
                body_low = body_err.lower()
                if "1010" in body_low:
                    return None, "ACESSO_BLOQUEADO"
                return None, "PERMISSAO_NEGADA"
            return None, f"HTTP_{code}"
        except URLError as e:
            print(f"[Cranio/xAI] URLError: {e}")
            return None, "REDE"
        except Exception as e:
            print(f"[Cranio/xAI] Erro inesperado: {e}")
            return None, "ERRO"

        escolhas = data.get("choices") or []
        if not escolhas:
            return None, "SEM_RESPOSTA"
        conteudo = ((escolhas[0].get("message") or {}).get("content") or "").strip()
        if not conteudo:
            return None, "SEM_TEXTO"
        return conteudo, None

    def _chamar_zai(pergunta: str, ctx: dict):
        api_key = os.getenv("ZAI_API_KEY", "").strip()
        model = os.getenv("ZAI_MODEL", "glm-5.1").strip() or "glm-5.1"
        if not api_key:
            return None, "SEM_CHAVE"

        crm_ctx = _montar_contexto_crm()
        hist = list(ctx.get("history") or [])[-6:]
        hist_txt = (
            "\n".join([f"Usuario: {h.get('q','')}\nCranio: {h.get('a','')}" for h in hist])
            if hist else "Sem historico anterior."
        )

        system_prompt = (
            "Voce e o Cranio, assistente inteligente de um CRM de ligacoes comerciais. "
            "Responda sempre em portugues-BR, com objetividade. "
            "Nao invente numeros; se faltar dado, diga claramente."
        )
        user_prompt = (
            f"=== DADOS ATUAIS DO CRM ===\n{crm_ctx}\n===========================\n\n"
            f"=== HISTORICO DA CONVERSA ===\n{hist_txt}\n=============================\n\n"
            f"Pergunta: {pergunta}"
        )

        body = {
            "model": model,
            "temperature": 0.3,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = Request(
            url="https://api.z.ai/api/paas/v4/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=14) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
        except HTTPError as e:
            code = getattr(e, "code", 0) or 0
            try:
                body_err = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                body_err = ""
            print(f"[Cranio/ZAI] HTTP {code}: {body_err}")
            if code == 429:
                if "credits" in body_err.lower() or "spending" in body_err.lower():
                    return None, "CREDITOS"
                return None, "QUOTA"
            if code == 503:
                return None, "INDISPONIVEL"
            if code == 401:
                return None, "CHAVE_INVALIDA"
            if code == 403:
                body_low = body_err.lower()
                if "1010" in body_low:
                    return None, "ACESSO_BLOQUEADO"
                return None, "PERMISSAO_NEGADA"
            return None, f"HTTP_{code}"
        except URLError as e:
            print(f"[Cranio/ZAI] URLError: {e}")
            return None, "REDE"
        except Exception as e:
            print(f"[Cranio/ZAI] Erro inesperado: {e}")
            return None, "ERRO"

        escolhas_z = data.get("choices") or []
        if not escolhas_z:
            return None, "SEM_RESPOSTA"
        conteudo_z = ((escolhas_z[0].get("message") or {}).get("content") or "").strip()
        if not conteudo_z:
            return None, "SEM_TEXTO"
        return conteudo_z, None

    def _chamar_groq(pergunta: str, ctx: dict):
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip() or "llama-3.3-70b-versatile"
        if not api_key:
            return None, "SEM_CHAVE"

        crm_ctx = _montar_contexto_crm()
        hist = list(ctx.get("history") or [])[-6:]
        hist_txt = (
            "\n".join([f"Usuario: {h.get('q','')}\nCranio: {h.get('a','')}" for h in hist])
            if hist else "Sem historico anterior."
        )

        system_prompt = (
            "Voce e o Cranio, assistente inteligente de um CRM de ligacoes comerciais. "
            "Responda sempre em portugues-BR, com objetividade. "
            "Nao invente numeros; se faltar dado, diga claramente."
        )
        user_prompt = (
            f"=== DADOS ATUAIS DO CRM ===\n{crm_ctx}\n===========================\n\n"
            f"=== HISTORICO DA CONVERSA ===\n{hist_txt}\n=============================\n\n"
            f"Pergunta: {pergunta}"
        )

        body = {
            "model": model,
            "temperature": 0.3,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = Request(
            url="https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=14) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            code = getattr(e, "code", 0) or 0
            try:
                body_err = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                body_err = ""
            print(f"[Cranio/Groq] HTTP {code}: {body_err}")
            if code == 429:
                return None, "QUOTA"
            if code == 401:
                return None, "CHAVE_INVALIDA"
            if code == 403:
                body_low = body_err.lower()
                if "1010" in body_low:
                    return None, "ACESSO_BLOQUEADO"
                return None, "PERMISSAO_NEGADA"
            if code == 503:
                return None, "INDISPONIVEL"
            return None, f"HTTP_{code}"
        except URLError as e:
            print(f"[Cranio/Groq] URLError: {e}")
            return None, "REDE"
        except Exception as e:
            print(f"[Cranio/Groq] Erro inesperado: {e}")
            return None, "ERRO"

        escolhas_g = data.get("choices") or []
        if not escolhas_g:
            return None, "SEM_RESPOSTA"
        conteudo_g = ((escolhas_g[0].get("message") or {}).get("content") or "").strip()
        if not conteudo_g:
            return None, "SEM_TEXTO"
        return conteudo_g, None

    def _responder_sem_ia(pergunta: str, ctx: dict) -> str:
        """Inteligência manual: responde com dados reais do CRM sem precisar de IA externa."""
        p = _normalize(pergunta)
        partes = []

        # ── Helpers internos ──────────────────────────────────────────────────
        def _meta_hoje():
            hoje = datetime.now().date()
            q = Ligacao.query.filter(func.date(Ligacao.data_hora) == hoje)
            if current_user.tipo in ("consultor", "televendas"):
                q = q.filter(Ligacao.consultor_id == current_user.id)
            total_lig   = q.count()
            total_vend  = q.filter(Ligacao.resultado == "comprou").count()
            meta        = current_user.meta_diaria or 0
            val_r       = q.filter(Ligacao.resultado == "comprou").with_entities(func.sum(Ligacao.valor_venda)).scalar()
            valor       = float(val_r or 0)
            taxa        = int(total_vend / total_lig * 100) if total_lig else 0
            linhas = [f"Desempenho de hoje ({hoje.strftime('%d/%m/%Y')}):"]
            if meta:
                pct = int(total_lig / meta * 100)
                status = "✅ Meta batida!" if total_lig >= meta else f"📊 {pct}% da meta ({total_lig}/{meta})"
                linhas.append(f"- Ligações: {total_lig} — {status}")
            else:
                linhas.append(f"- Ligações realizadas: {total_lig}")
            linhas.append(f"- Vendas: {total_vend} | Conversão: {taxa}%" + (f" | R$ {valor:,.2f}" if valor else ""))
            perdidos = q.filter(Ligacao.resultado == "nao_comprou").count()
            retornar = q.filter(Ligacao.resultado == "retornar").count()
            linhas.append(f"- Não comprou: {perdidos} | Retornar: {retornar}")
            return "\n".join(linhas)

        def _janela_inativacao():
            agora = datetime.now()
            limite = agora - timedelta(days=25)
            try:
                q = _base_clientes_visiveis().filter(
                    Cliente.ultima_ligacao.isnot(None),
                    Cliente.ultima_ligacao <= limite,
                    Cliente.status == "ativo",
                )
                total = q.count()
                if not total:
                    return "Nenhum cliente ativo próximo da janela de inativação (25+ dias sem contato)."
                exemplos = [c.nome for c in q.order_by(Cliente.ultima_ligacao.asc()).limit(4).all()]
                return (
                    f"⚠️ {total} cliente(s) com 25+ dias sem contato (risco de inativação).\n"
                    f"Priorizar: {', '.join(exemplos)}" + (" e outros." if total > 4 else ".")
                )
            except Exception:
                return "Não foi possível calcular janela de inativação agora."

        def _follow_ups():
            hoje = datetime.now().date()
            try:
                q = _base_clientes_visiveis().filter(
                    Cliente.proxima_ligacao.isnot(None),
                    func.date(Cliente.proxima_ligacao) == hoje,
                )
                total = q.count()
                if not total:
                    return "Nenhum follow-up agendado para hoje."
                nomes = [c.nome for c in q.order_by(Cliente.proxima_ligacao.asc()).limit(5).all()]
                return f"📞 {total} follow-up(s) para hoje: {', '.join(nomes)}" + (" e outros." if total > 5 else ".")
            except Exception:
                return "Não foi possível listar follow-ups agora."

        # ── Análise completa / panorama / como estou ──────────────────────────
        if any(k in p for k in (
            "analise", "panorama", "como estou", "como ta", "como esta",
            "situacao", "overview", "geral", "tudo", "tudo bem",
        )):
            partes.append(_meta_hoje())
            partes.append(_retornos_atrasados())
            partes.append(_janela_inativacao())
            partes.append(_proximos_ligar())

        # ── Resultado / vendas / desempenho / meta ────────────────────────────
        if not partes and any(k in p for k in (
            "vend", "comprou", "conversao", "taxa", "faturamento", "valor",
            "quanto vendi", "resultado", "desempenho", "meta", "fechamento",
            "resumo", "hoje", "dia", "como fui", "minha meta", "bati", "batendo",
        )):
            partes.append(_meta_hoje())

        # ── Ligações / atividade ──────────────────────────────────────────────
        if not partes and any(k in p for k in (
            "ligacao", "ligacoes", "quantas ligacoes", "fiz hoje", "atividade", "quantas fiz",
        )):
            partes.append(_meta_hoje())

        # ── Janela de inativação / risco ──────────────────────────────────────
        if any(k in p for k in (
            "inativ", "risco", "janela", "sem contato", "sumidos", "esquecidos", "25 dias", "30 dias",
        )):
            partes.append(_janela_inativacao())

        # ── Follow-ups / agenda de hoje ───────────────────────────────────────
        if any(k in p for k in (
            "follow", "followup", "agendado hoje", "ligar hoje", "para hoje",
        )):
            partes.append(_follow_ups())

        # ── Ranking / quem mais ───────────────────────────────────────────────
        if any(k in p for k in (
            "ranking", "melhor", "mais ligou", "top", "lider", "operador", "consultor", "produtivo",
        )):
            partes.append(_quem_mais_ligou(p))

        # ── Total de clientes / carteira ──────────────────────────────────────
        if any(k in p for k in (
            "quantos clientes", "total clientes", "carteira", "minha base",
            "clientes no total", "quantos temos", "base de clientes",
        )):
            partes.append(_total_clientes())

        # ── Retornos atrasados ────────────────────────────────────────────────
        if any(k in p for k in (
            "atrasado", "vencido", "retorno", "em atraso", "pendente", "nao liguei", "esqueci",
        )):
            partes.append(_retornos_atrasados())

        # ── Próximos agendados ────────────────────────────────────────────────
        if any(k in p for k in (
            "proximo", "agenda", "agendado", "quando ligar", "quem ligar", "prioridade", "agora",
        )):
            partes.append(_proximos_ligar())

        # ── Se nada casou: retorna panorama completo do CRM ──────────────────
        if not partes:
            ctx_txt = _montar_contexto_crm()
            return (
                "Minha IA está offline, mas aqui está o panorama atual do CRM:\n\n"
                + ctx_txt
                + "\n\nPara mais detalhes tente: 'resumo de hoje', 'retornos atrasados' ou 'janela de inativação'."
            )

        # Deduplicar e juntar se múltiplos blocos
        vistos, unico = set(), []
        for bloco in partes:
            if bloco not in vistos:
                vistos.add(bloco)
                unico.append(bloco)

        prefixo = "📊 IA offline — dados diretos do CRM:\n\n"
        return prefixo + "\n\n─────\n\n".join(unico)

    def _responder_com_ia_ou_folga(pergunta: str, ctx: dict):
        permitido, uso, limite = _permitir_ia_hoje()
        if not permitido:
            return (
                "Cranio esta de folga agora: acabou a energia de IA de hoje. "
                f"(limite {limite}/dia por usuario). Tenta novamente amanha."
            )

        # Primária: Groq (Llama 3.3 70B — gratuito, generoso)
        resposta_groq, erro_groq = _chamar_groq(pergunta, ctx)
        if resposta_groq:
            return resposta_groq

        # Fallback: Gemini
        resposta, erro = _chamar_gemini(pergunta, ctx)
        if resposta:
            return resposta

        erros = f"Groq={erro_groq} | Gemini={erro}"
        print(f"[Cranio] Todas IAs falharam: {erros}")

        todos = (erro_groq, erro)
        sem_chave      = all(e == "SEM_CHAVE" for e in todos)
        chave_invalida = "CHAVE_INVALIDA" in todos
        acesso_bloqueado = "ACESSO_BLOQUEADO" in todos
        permissao_negada = "PERMISSAO_NEGADA" in todos
        creditos_xai   = False
        indisponivel   = "INDISPONIVEL" in todos
        quota          = "QUOTA" in todos

        if sem_chave:
            return (
                "Ainda nao entendi essa pergunta. Tenta assim:\n"
                "- resumo de hoje\n"
                "- quem mais ligou hoje\n"
                "- quantos clientes temos no total\n"
                "- retornos atrasados\n"
                "- proximos para ligar\n"
                "- buscar cliente <nome ou cnpj>"
            )

        if chave_invalida:
            return (
                "Hoje eu acordei sem cracha de acesso na portaria da IA. "
                "Me chama de novo daqui a pouco que eu tento entrar pela porta da frente."
            )

        # IA indisponível por qualquer motivo → inteligência manual com dados do CRM
        return _responder_sem_ia(pergunta, ctx)

    def _resolver_pergunta(pergunta: str):
        ctx = _get_ctx()
        pergunta_original = str(pergunta or "").strip()
        p = _normalize(pergunta_original)

        if not p:
            resposta = "Manda sua pergunta. Ex.: 'resumo de hoje', 'retornos atrasados' ou 'buscar cliente'."
            _record_history(ctx, pergunta_original, resposta)
            _save_ctx(ctx)
            return resposta

        # 0) Saudações e interações sociais — sem IA, sem banco
        _SAUDACOES = ("ola", "oi", "eai", "e ai", "hey", "hello", "bom dia", "boa tarde", "boa noite", "boa noite cranio", "ola cranio", "oi cranio")
        _AGRADEC   = ("obrigado", "obrigada", "valeu", "thanks", "brigado", "brigada", "muito obrigado", "muito obrigada")
        _HUMOR     = ("como vai", "como voce ta", "tudo bem", "tudo bom", "como esta", "ta bem", "beleza", "firmeza")
        if any(p == k or p.startswith(k) for k in _SAUDACOES):
            nome = current_user.nome.split()[0] if current_user.nome else "chefe"
            resposta = f"Oi, {nome}! T\u00f4 aqui, ligado nos seus n\u00fameros. Pode perguntar \ud83e\udde0"
            _record_history(ctx, pergunta_original, resposta, "saudacao")
            _save_ctx(ctx)
            return resposta
        if any(k in p for k in _AGRADEC):
            resposta = "Dispon\u00edva! Qualquer coisa \u00e9 s\u00f3 chamar."
            _record_history(ctx, pergunta_original, resposta, "agradecimento")
            _save_ctx(ctx)
            return resposta
        if any(k in p for k in _HUMOR):
            resposta = "T\u00f4 \u00f3timo! Processando dados e observando a equipe. E voc\u00ea, como t\u00e1 indo hoje?"
            _record_history(ctx, pergunta_original, resposta, "humor")
            _save_ctx(ctx)
            return resposta

        # 1) Seleção de cliente pendente (sem IA, sem keyword)
        resposta_pendente = _selecionar_cliente_pendente(ctx, p)
        if resposta_pendente:
            _record_history(ctx, pergunta_original, resposta_pendente, "buscar_cliente")
            _save_ctx(ctx)
            return resposta_pendente

        # 2) Busca explícita de cliente no banco (sem IA)
        if any(k in p for k in ("buscar", "cnpj", "encontrar", "procurar")):
            termo = re.sub(r"\b(buscar|cliente|cnpj|encontrar|procura|procurar)\b", " ", pergunta_original, flags=re.I)
            resposta = _buscar_cliente(termo.strip(), ctx)
            _record_history(ctx, pergunta_original, resposta, "buscar_cliente")
            _save_ctx(ctx)
            return resposta

        # 3) Queries longas/complexas vão direto pra IA com contexto do CRM
        #    Keyword matching por substring falha em perguntas compostas (ex: "quantos
        #    clientes inativos foram contatados") — a IA lida melhor com linguagem natural.
        _groq_key   = os.getenv("GROQ_API_KEY", "").strip()
        _gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        if (_groq_key or _gemini_key) and len(p.split()) > 4:
            resposta = _responder_com_ia_ou_folga(pergunta_original, ctx)
            _record_history(ctx, pergunta_original, resposta, "ia_direta")
            _save_ctx(ctx)
            return resposta

        # 4) Handlers locais para comandos curtos e inequívocos (≤ 4 palavras)
        if any(k in p for k in ("quem mais ligou", "quem ligou mais", "ranking", "top operador", "top consultor", "melhor consultor", "mais produtivo")):
            resposta = _quem_mais_ligou(p)
            _record_history(ctx, pergunta_original, resposta, "top_ligacoes")
            _save_ctx(ctx)
            return resposta

        if any(k in p for k in ("resumo", "hoje", "fechamento", "como fui", "meu resultado", "minha meta", "meu desempenho", "minhas ligacoes", "minhas vendas", "quanto vendi")):
            resposta = _resumo_hoje()
            _record_history(ctx, pergunta_original, resposta, "resumo_hoje")
            _save_ctx(ctx)
            return resposta

        if any(k in p for k in ("total clientes", "total de clientes", "clientes no total", "qtd clientes", "minha carteira", "carteira de clientes")) or re.search(r"^quantos clientes\s*\??$", p):
            resposta = _total_clientes()
            _record_history(ctx, pergunta_original, resposta, "total_clientes")
            _save_ctx(ctx)
            return resposta

        if any(k in p for k in ("atrasado", "atrasados", "retorno vencido", "retornos vencidos", "em atraso")):
            resposta = _retornos_atrasados()
            _record_history(ctx, pergunta_original, resposta, "retornos_atrasados")
            _save_ctx(ctx)
            return resposta

        if (
            ("inativ" in p and "mes" in p)
            or any(k in p for k in ("inativacao esse mes", "inativam esse mes", "vao ser inativados"))
        ):
            resposta = _q_clientes_inativam_no_mes(p)
            _record_history(ctx, pergunta_original, resposta, "inativacao_mes")
            _save_ctx(ctx)
            return resposta

        if any(k in p for k in ("proximo", "proximos", "agendado", "agendados", "agenda", "quando ligar", "proximo retorno")):
            resposta = _proximos_ligar()
            _record_history(ctx, pergunta_original, resposta, "proximos_ligar")
            _save_ctx(ctx)
            return resposta

        if re.search(r"\bcliente\b", p):
            termo = re.sub(r"\b(buscar|cliente|cnpj|encontrar|procura|procurar)\b", " ", pergunta_original, flags=re.I)
            resposta = _buscar_cliente(termo.strip(), ctx)
            _record_history(ctx, pergunta_original, resposta, "buscar_cliente")
            _save_ctx(ctx)
            return resposta

        # 5) Fallback: IA ou mensagem de ajuda
        resposta = _responder_com_ia_ou_folga(pergunta_original, ctx)
        _record_history(ctx, pergunta_original, resposta, "fallback_ia")
        _save_ctx(ctx)
        return resposta

    @app.route("/cranio")
    @login_required
    def cranio_page():
        return render_template("cranio.html")

    @app.route("/api/cranio/perguntar", methods=["POST"])
    @login_required
    def cranio_perguntar():
        try:
            payload = request.get_json(silent=True) or {}
            pergunta = str(payload.get("pergunta") or "").strip()
            resposta = _resolver_pergunta(pergunta)
            return jsonify({"ok": True, "resposta": resposta, "status": _estado_cranio(_get_ctx())})
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    @app.route("/api/cranio/status", methods=["GET"])
    @login_required
    def cranio_status():
        try:
            return jsonify({"ok": True, "status": _estado_cranio(_get_ctx())})
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500


    @app.route("/api/cranio/lembrete", methods=["GET"])
    @login_required
    def cranio_lembrete():
        try:
            agora = datetime.now()
            forcar = str(request.args.get("forcar") or "").strip() in ("1", "true", "True")
            pool_mode = str(request.args.get("pool") or "").strip() in ("1", "true", "True")

            q_visiveis = (
                _base_clientes_visiveis()
                .join(Usuario, Cliente.consultor_id == Usuario.id)
                .filter(Usuario.ativo == True)
            )

            total_atrasados = q_visiveis.filter(
                Cliente.proxima_ligacao.isnot(None),
                Cliente.proxima_ligacao < agora,
            ).count()

            janela = agora + timedelta(hours=2)
            total_proximos_2h = q_visiveis.filter(
                Cliente.proxima_ligacao.isnot(None),
                Cliente.proxima_ligacao >= agora,
                Cliente.proxima_ligacao <= janela,
            ).count()

            # Mesma base da aba "Próximos Inativação":
            # clientes com último pedido entre 151 e 180 dias.
            limite_151 = agora - timedelta(days=151)
            limite_180 = agora - timedelta(days=180)
            q_prox_inat = q_visiveis.filter(
                Cliente.ultimo_pedido_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.between(limite_180, limite_151),
            )
            total_prox_inat = q_prox_inat.count()
            data_mais_antiga = q_prox_inat.with_entities(func.min(Cliente.ultimo_pedido_oracle)).scalar()
            dias_min_inativacao = None
            total_inativa_no_prazo_min = 0
            if data_mais_antiga:
                dias_sem = (agora - data_mais_antiga).days
                dias_min_inativacao = max(0, 181 - dias_sem)
                # Conta apenas quem realmente inativa dentro do menor prazo detectado.
                # Ex.: se o menor prazo é 2 dias, considera clientes com 179+ dias sem pedido.
                limite_prazo_min = agora - timedelta(days=max(0, 181 - dias_min_inativacao))
                total_inativa_no_prazo_min = q_prox_inat.filter(
                    Cliente.ultimo_pedido_oracle <= limite_prazo_min
                ).count()

            # Subconjuntos de inativação para lembretes em faixas
            limite_166 = agora - timedelta(days=166)  # inativam em <= 15 dias
            total_inativa_15 = q_prox_inat.filter(Cliente.ultimo_pedido_oracle <= limite_166).count()
            total_inativa_30 = total_prox_inat  # por definição da carteira 151-180

            # Entraram hoje na carteira 90-150 (borda de 90 dias sem pedido hoje)
            inicio_dia = datetime.combine(agora.date(), datetime.min.time())
            fim_dia = inicio_dia + timedelta(days=1)
            ini_90 = inicio_dia - timedelta(days=90)
            fim_90 = fim_dia - timedelta(days=90)
            total_entrou_90_hoje = q_visiveis.filter(
                Cliente.ultimo_pedido_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle >= ini_90,
                Cliente.ultimo_pedido_oracle < fim_90,
            ).count()

            hoje = agora.date()
            q_lig = Ligacao.query.filter(func.date(Ligacao.data_hora) == hoje)
            if current_user.tipo in ("consultor", "televendas"):
                q_lig = q_lig.filter(Ligacao.consultor_id == current_user.id)
            lig_hoje = q_lig.count()

            lembretes = []
            if total_atrasados > 0:
                lembretes.append(
                    {
                        "id": "retornos_atrasados",
                        "priority": "high",
                        "message": (
                            f"Lembrete: você tem {total_atrasados} retorno"
                            f"{'s' if total_atrasados != 1 else ''} atrasado"
                            f"{'s' if total_atrasados != 1 else ''}. Priorize essa fila."
                        ),
                    }
                )

            if total_inativa_no_prazo_min > 0 and dias_min_inativacao is not None:
                lembretes.append(
                    {
                        "id": "inativa_mais_proximo",
                        "priority": "normal",
                        "message": (
                            f"Lembrete: {total_inativa_no_prazo_min} cliente"
                            f"{'s' if total_inativa_no_prazo_min != 1 else ''} "
                            f"{'entram' if total_inativa_no_prazo_min != 1 else 'entra'} em inativação em até "
                            f"{dias_min_inativacao} dia{'s' if dias_min_inativacao != 1 else ''}."
                        ),
                    }
                )
            if total_inativa_15 > 0:
                lembretes.append(
                    {
                        "id": "inativa_15_dias",
                        "priority": "normal",
                        "message": (
                            f"Atenção: {total_inativa_15} cliente"
                            f"{'s' if total_inativa_15 != 1 else ''} podem inativar em até 15 dias."
                        ),
                    }
                )
            if total_inativa_30 > 0:
                lembretes.append(
                    {
                        "id": "inativa_30_dias",
                        "priority": "normal",
                        "message": (
                            f"Radar: {total_inativa_30} cliente"
                            f"{'s' if total_inativa_30 != 1 else ''} estão na janela de inativação de até 30 dias."
                        ),
                    }
                )
            if total_entrou_90_hoje > 0:
                lembretes.append(
                    {
                        "id": "entrou_90_hoje",
                        "priority": "normal",
                        "message": (
                            f"Movimento de carteira: {total_entrou_90_hoje} cliente"
                            f"{'s' if total_entrou_90_hoje != 1 else ''} entraram hoje em Sem Pedido 90-150."
                        ),
                    }
                )

            if total_proximos_2h > 0:
                lembretes.append(
                    {
                        "id": "retornos_2h",
                        "priority": "normal",
                        "message": (
                            f"Lembrete: {total_proximos_2h} retorno"
                            f"{'s' if total_proximos_2h != 1 else ''} vencem nas próximas 2h."
                        ),
                    }
                )

            if agora.hour >= 10 and lig_hoje == 0:
                lembretes.append(
                    {
                        "id": "sem_ligacao_hoje",
                        "priority": "normal",
                        "message": "Dica do Crânio: ainda sem ligações hoje. Vale puxar os próximos retornos.",
                    }
                )

            if pool_mode:
                if lembretes:
                    return jsonify({"ok": True, "show": True, "pool": lembretes})
                if forcar:
                    return jsonify(
                        {
                            "ok": True,
                            "show": True,
                            "pool": [
                                {
                                    "id": "all_ok",
                                    "priority": "normal",
                                    "message": "Crânio: tudo sob controle por agora. Se quiser, posso te lembrar depois de novo.",
                                }
                            ],
                        }
                    )
                return jsonify({"ok": True, "show": False, "pool": []})

            if forcar:
                if lembretes:
                    escolhido = random.choice(lembretes)
                    return jsonify({"ok": True, "show": True, **escolhido})
                return jsonify(
                    {
                        "ok": True,
                        "show": True,
                        "priority": "normal",
                        "message": "Crânio: tudo sob controle por agora. Se quiser, posso te lembrar depois de novo.",
                    }
                )

            if total_atrasados > 0:
                msg = (
                    f"Lembrete: você tem {total_atrasados} retorno"
                    f"{'s' if total_atrasados != 1 else ''} atrasado"
                    f"{'s' if total_atrasados != 1 else ''}. Priorize essa fila."
                )
                return jsonify({"ok": True, "show": True, "priority": "high", "message": msg})

            if total_inativa_no_prazo_min > 0 and dias_min_inativacao is not None:
                msg = (
                    f"Lembrete: {total_inativa_no_prazo_min} cliente"
                    f"{'s' if total_inativa_no_prazo_min != 1 else ''} "
                    f"{'entram' if total_inativa_no_prazo_min != 1 else 'entra'} em inativação em até "
                    f"{dias_min_inativacao} dia{'s' if dias_min_inativacao != 1 else ''}."
                )
                return jsonify({"ok": True, "show": True, "priority": "normal", "message": msg})

            if total_proximos_2h > 0:
                msg = (
                    f"Lembrete: {total_proximos_2h} retorno"
                    f"{'s' if total_proximos_2h != 1 else ''} vencem nas próximas 2h."
                )
                return jsonify({"ok": True, "show": True, "priority": "normal", "message": msg})

            if agora.hour >= 10 and lig_hoje == 0:
                return jsonify(
                    {
                        "ok": True,
                        "show": True,
                        "priority": "normal",
                        "message": "Dica do Crânio: ainda sem ligações hoje. Vale puxar os próximos retornos.",
                    }
                )

            return jsonify({"ok": True, "show": False})
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500
