from datetime import datetime, timedelta

from flask_mail import Message
from sqlalchemy import and_, case, func

from core.config import MAIL_PASSWORD, MAIL_RECIPIENTS
from core.extensions import db, mail
from core.helpers import _kfmt, _percent, formatar_dinheiro
from core.models import Cliente, Ligacao, SyncResumoDiario, Usuario


def _build_assunto_relatorio():
    agora = datetime.now()
    hoje_txt = agora.strftime("%d/%m/%Y")
    inicio_mes = datetime(agora.year, agora.month, 1)
    limite_90 = agora - timedelta(days=90)
    limite_150 = agora - timedelta(days=150)
    limite_181 = agora - timedelta(days=181)
    limite_151 = agora - timedelta(days=151)
    limite_180 = agora - timedelta(days=180)
    limite_730 = agora - timedelta(days=730)

    total_mes = db.session.query(func.count(Ligacao.id)).filter(Ligacao.data_hora >= inicio_mes).scalar() or 0
    compras_mes = (
        db.session.query(func.count(Ligacao.id))
        .filter(Ligacao.data_hora >= inicio_mes, Ligacao.resultado == "comprou")
        .scalar()
    ) or 0
    conv_mes = _percent(compras_mes, total_mes)

    filtro_oracle_base = [
        Cliente.ativo == True,
        Cliente.cd_cliente_oracle.isnot(None),
        Cliente.ultimo_pedido_oracle.isnot(None),
    ]
    sem_pedidos_90_150 = (
        db.session.query(func.count(Cliente.id))
        .filter(*filtro_oracle_base, Cliente.ultimo_pedido_oracle.between(limite_150, limite_90))
        .scalar()
    ) or 0
    inativos_181_730 = (
        db.session.query(func.count(Cliente.id))
        .filter(*filtro_oracle_base, Cliente.ultimo_pedido_oracle.between(limite_730, limite_181))
        .scalar()
    ) or 0
    proximos_inativacao_151_180 = (
        db.session.query(func.count(Cliente.id))
        .filter(*filtro_oracle_base, Cliente.ultimo_pedido_oracle.between(limite_180, limite_151))
        .scalar()
    ) or 0

    return (
        f"Relatório Diário CRM {hoje_txt} | "
        f"SP90-150: {int(sem_pedidos_90_150)} | "
        f"Risco151-180: {int(proximos_inativacao_151_180)} | "
        f"Inativos: {int(inativos_181_730)} | "
        f"ConvMês: {conv_mes:.1f}%"
    )


def _query_operadores_detalhe(tipo_operador, hoje, desde7, inicio_mes):
    return (
        db.session.query(
            Usuario.id,
            Usuario.nome,
            Usuario.meta_diaria,
            func.sum(case((func.date(Ligacao.data_hora) == hoje, 1), else_=0)).label("lig_hoje"),
            func.sum(case((Ligacao.data_hora >= desde7, 1), else_=0)).label("lig_7d"),
            func.sum(case((Ligacao.data_hora >= inicio_mes, 1), else_=0)).label("lig_mes"),
            func.sum(case((and_(Ligacao.data_hora >= inicio_mes, Ligacao.resultado == "comprou"), 1), else_=0)).label("comprou_mes"),
            func.sum(case((and_(Ligacao.data_hora >= inicio_mes, Ligacao.resultado == "relacionamento"), 1), else_=0)).label("relacionamento_mes"),
            func.sum(case((and_(Ligacao.data_hora >= inicio_mes, Ligacao.resultado == "nao_comprou"), 1), else_=0)).label("nao_comprou_mes"),
            func.sum(case((and_(Ligacao.data_hora >= inicio_mes, Ligacao.resultado == "sem_interesse"), 1), else_=0)).label("sem_interesse_mes"),
            func.sum(case((and_(Ligacao.data_hora >= inicio_mes, Ligacao.resultado == "retornar"), 1), else_=0)).label("retornar_mes"),
        )
        .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
        .filter(Usuario.tipo == tipo_operador, Usuario.ativo == True)
        .group_by(Usuario.id, Usuario.nome, Usuario.meta_diaria)
        .order_by(Usuario.nome)
        .all()
    )


def _montar_linhas_operadores(rows, incluir_meta=True):
    linhas = ""
    for _id, nome, meta, lig_hoje, lig_7d, lig_mes, comprou_mes, relacionamento_mes, nao_comprou_mes, sem_interesse_mes, retornar_mes in rows:
        lig_hoje = int(lig_hoje or 0)
        lig_7d = int(lig_7d or 0)
        lig_mes = int(lig_mes or 0)
        comprou_mes = int(comprou_mes or 0)
        relacionamento_mes = int(relacionamento_mes or 0)
        nao_comprou_mes = int(nao_comprou_mes or 0)
        sem_interesse_mes = int(sem_interesse_mes or 0)
        retornar_mes = int(retornar_mes or 0)
        meta_int = int(meta or 0)
        ating_meta = _percent(lig_hoje, meta_int) if meta_int else 0.0

        if incluir_meta:
            meta_col = str(meta_int)
            meta_pct_col = f"{ating_meta:.1f}%" if meta_int else "-"
        else:
            meta_col = "-"
            meta_pct_col = "-"

        linhas += (
            "<tr>"
            f"<td style='padding:10px; border-top:1px solid #edf2f7;'>{nome}</td>"
            f"<td style='padding:10px; border-top:1px solid #edf2f7; text-align:right'>{lig_hoje}</td>"
            f"<td style='padding:10px; border-top:1px solid #edf2f7; text-align:right'>{lig_7d}</td>"
            f"<td style='padding:10px; border-top:1px solid #edf2f7; text-align:right'>{lig_mes}</td>"
            f"<td style='padding:10px; border-top:1px solid #edf2f7; text-align:right'>{relacionamento_mes}</td>"
            f"<td style='padding:10px; border-top:1px solid #edf2f7; text-align:right'>{comprou_mes}</td>"
            f"<td style='padding:10px; border-top:1px solid #edf2f7; text-align:right'>{nao_comprou_mes}</td>"
            f"<td style='padding:10px; border-top:1px solid #edf2f7; text-align:right'>{sem_interesse_mes}</td>"
            f"<td style='padding:10px; border-top:1px solid #edf2f7; text-align:right'>{retornar_mes}</td>"
            f"<td style='padding:10px; border-top:1px solid #edf2f7; text-align:right'>{meta_col}</td>"
            f"<td style='padding:10px; border-top:1px solid #edf2f7; text-align:right'>{meta_pct_col}</td>"
            "</tr>"
        )
    return linhas


def _montar_insights_basicos(
    total_hoje,
    total_7,
    total_ontem,
    media_mesmo_dia_semana,
    conv_mes,
    total_sem_pedidos_90_150,
    total_inativos_181_730,
    inativos_entraram_hoje,
    inativos_sairam_hoje,
    detalhe_consultores,
    detalhe_televendas,
):
    linhas = []

    def _add(prio, icone, texto):
        cores = {
            "CRITICO": ("#b91c1c", "#fef2f2"),
            "ATENCAO": ("#92400e", "#fffbeb"),
            "OK": ("#166534", "#f0fdf4"),
        }
        fg, bg = cores.get(prio, ("#1f2937", "#f8fafc"))
        badge = (
            f"<span style='display:inline-block; min-width:70px; text-align:center; "
            f"padding:2px 8px; margin-right:8px; border-radius:999px; font-size:11px; "
            f"font-weight:700; color:{fg}; background:{bg}; border:1px solid {fg};'>{prio}</span>"
        )
        linhas.append(
            "<li style='margin:0 0 8px 0; padding:8px 10px; border:1px solid #e5e7eb; border-radius:8px; background:#fff;'>"
            f"{badge}{icone} {texto}</li>"
        )

    media_7d = (float(total_7) / 7.0) if total_7 else 0.0
    if media_7d > 0:
        variacao_7d = ((float(total_hoje) - media_7d) / media_7d) * 100.0
        if variacao_7d <= -20:
            _add("CRITICO", "&#9888;&#65039;", f"Volume de hoje caiu {abs(variacao_7d):.1f}% vs média dos últimos 7 dias.")
        elif variacao_7d <= -8:
            _add("ATENCAO", "&#128201;", f"Volume de hoje está {abs(variacao_7d):.1f}% abaixo da média de 7 dias.")
        elif variacao_7d >= 12:
            _add("OK", "&#128640;", f"Volume de hoje está {variacao_7d:.1f}% acima da média de 7 dias.")
        else:
            _add("OK", "&#128205;", "Ritmo do dia está estável em relação à média de 7 dias.")

    if total_ontem > 0:
        variacao_ontem = ((float(total_hoje) - float(total_ontem)) / float(total_ontem)) * 100.0
        if variacao_ontem < 0:
            _add("ATENCAO", "&#8600;&#65039;", f"Hoje está {abs(variacao_ontem):.1f}% abaixo de ontem ({total_hoje} vs {total_ontem}).")
        else:
            _add("OK", "&#8599;&#65039;", f"Hoje está {variacao_ontem:.1f}% acima de ontem ({total_hoje} vs {total_ontem}).")

    if media_mesmo_dia_semana > 0:
        variacao_semana = ((float(total_hoje) - media_mesmo_dia_semana) / media_mesmo_dia_semana) * 100.0
        if variacao_semana < -10:
            _add("ATENCAO", "&#128467;&#65039;", f"Hoje está {abs(variacao_semana):.1f}% abaixo da média deste mesmo dia da semana.")
        else:
            _add("OK", "&#128467;&#65039;", f"Hoje está alinhado ao padrão semanal ({variacao_semana:.1f}% de variação).")

    if conv_mes >= 30:
        _add("OK", "&#9989;", f"Conversão do mês está saudável em {conv_mes:.1f}%.")
    elif conv_mes >= 20:
        _add("ATENCAO", "&#128993;", f"Conversão do mês em atenção: {conv_mes:.1f}%.")
    else:
        _add("CRITICO", "&#128308;", f"Conversão do mês baixa: {conv_mes:.1f}%.")

    if inativos_entraram_hoje > inativos_sairam_hoje:
        saldo = inativos_entraram_hoje - inativos_sairam_hoje
        prio = "CRITICO" if saldo >= 15 else "ATENCAO"
        _add(prio, "&#128200;", f"Base de inativos cresceu em +{saldo} hoje (E:{inativos_entraram_hoje} / S:{inativos_sairam_hoje}).")
    elif inativos_sairam_hoje > inativos_entraram_hoje:
        saldo = inativos_sairam_hoje - inativos_entraram_hoje
        _add("OK", "&#128201;", f"Base de inativos reduziu em -{saldo} hoje (E:{inativos_entraram_hoje} / S:{inativos_sairam_hoje}).")
    else:
        _add("OK", "&#10134;", "Movimento de inativos neutro hoje (entradas = saídas).")

    def _top_vendas(rows):
        if not rows:
            return None
        top = max(rows, key=lambda r: int(r[6] or 0))
        return top[1], int(top[6] or 0)

    top_c = _top_vendas(detalhe_consultores)
    top_t = _top_vendas(detalhe_televendas)
    if top_c:
        _add("OK", "&#127942;", f"Destaque consultores no mês: {top_c[0]} com {top_c[1]} vendas.")
    if top_t:
        _add("OK", "&#127942;", f"Destaque televendas no mês: {top_t[0]} com {top_t[1]} vendas.")

    if total_inativos_181_730 > total_sem_pedidos_90_150 * 3:
        _add("ATENCAO", "&#127919;", "Ação do dia: priorizar televendas na carteira de inativos (base alta).")
    elif conv_mes < 20:
        _add("ATENCAO", "&#129517;", "Ação do dia: reforçar follow-up de retornos e clientes sem interesse recente.")
    else:
        _add("OK", "&#129517;", "Ação do dia: manter ritmo equilibrado entre consultores e televendas.")

    return "".join(linhas[:7])


def build_relatorio_html():
    hoje = datetime.now().date()
    agora = datetime.now()
    ontem = hoje - timedelta(days=1)
    desde7 = agora - timedelta(days=7)
    inicio_mes = datetime(agora.year, agora.month, 1)
    limite_90 = agora - timedelta(days=90)
    limite_150 = agora - timedelta(days=150)
    limite_181 = agora - timedelta(days=181)
    limite_151 = agora - timedelta(days=151)
    limite_180 = agora - timedelta(days=180)
    limite_730 = agora - timedelta(days=730)

    total_hoje = db.session.query(func.count(Ligacao.id)).filter(func.date(Ligacao.data_hora) == hoje).scalar() or 0
    total_ontem = db.session.query(func.count(Ligacao.id)).filter(func.date(Ligacao.data_hora) == ontem).scalar() or 0
    total_7 = db.session.query(func.count(Ligacao.id)).filter(Ligacao.data_hora >= desde7).scalar() or 0
    total_mes = db.session.query(func.count(Ligacao.id)).filter(Ligacao.data_hora >= inicio_mes).scalar() or 0

    dias_mesmo_dia = [hoje - timedelta(days=7 * i) for i in range(1, 5)]
    contagens_semanais = [
        (db.session.query(func.count(Ligacao.id)).filter(func.date(Ligacao.data_hora) == d).scalar() or 0)
        for d in dias_mesmo_dia
    ]
    media_mesmo_dia_semana = (sum(contagens_semanais) / len(contagens_semanais)) if contagens_semanais else 0.0

    resultados = dict(
        (r or "nao_comprou", int(c))
        for r, c in (
            db.session.query(Ligacao.resultado, func.count(Ligacao.id))
            .filter(Ligacao.data_hora >= inicio_mes)
            .group_by(Ligacao.resultado)
            .all()
        )
    )
    compras_mes = resultados.get("comprou", 0)
    conv_mes = _percent(compras_mes, total_mes)
    receita_mes = (
        db.session.query(func.sum(Ligacao.valor_venda))
        .filter(Ligacao.data_hora >= inicio_mes, Ligacao.resultado == "comprou")
        .scalar()
    ) or 0

    filtro_oracle_base = [
        Cliente.ativo == True,
        Cliente.cd_cliente_oracle.isnot(None),
        Cliente.ultimo_pedido_oracle.isnot(None),
    ]
    total_clientes_normais = (
        db.session.query(func.count(Cliente.id))
        .filter(Cliente.ativo == True, Cliente.cd_cliente_oracle.is_(None))
        .scalar()
    ) or 0
    total_sem_pedidos_90_150 = (
        db.session.query(func.count(Cliente.id))
        .filter(*filtro_oracle_base, Cliente.ultimo_pedido_oracle.between(limite_150, limite_90))
        .scalar()
    ) or 0
    total_inativos_181_730 = (
        db.session.query(func.count(Cliente.id))
        .filter(*filtro_oracle_base, Cliente.ultimo_pedido_oracle.between(limite_730, limite_181))
        .scalar()
    ) or 0
    total_proximos_inativacao_151_180 = (
        db.session.query(func.count(Cliente.id))
        .filter(*filtro_oracle_base, Cliente.ultimo_pedido_oracle.between(limite_180, limite_151))
        .scalar()
    ) or 0

    ultima_sync = db.session.query(func.max(Cliente.data_ultima_sincronizacao)).scalar()
    sync_txt = ultima_sync.strftime("%d/%m/%Y %H:%M") if ultima_sync else "Nao identificado"

    resumo_sync_hoje = SyncResumoDiario.query.filter_by(data_ref=hoje).first()
    inativos_entraram_hoje = int(resumo_sync_hoje.inativos_entraram) if resumo_sync_hoje else 0
    inativos_sairam_hoje = int(resumo_sync_hoje.inativos_sairam) if resumo_sync_hoje else 0

    detalhe_consultores = _query_operadores_detalhe("consultor", hoje, desde7, inicio_mes)
    detalhe_televendas = _query_operadores_detalhe("televendas", hoje, desde7, inicio_mes)
    linhas_consultores = _montar_linhas_operadores(detalhe_consultores, incluir_meta=True)
    linhas_televendas = _montar_linhas_operadores(detalhe_televendas, incluir_meta=False)

    insights_html = _montar_insights_basicos(
        total_hoje=total_hoje,
        total_7=total_7,
        total_ontem=total_ontem,
        media_mesmo_dia_semana=media_mesmo_dia_semana,
        conv_mes=conv_mes,
        total_sem_pedidos_90_150=total_sem_pedidos_90_150,
        total_inativos_181_730=total_inativos_181_730,
        inativos_entraram_hoje=inativos_entraram_hoje,
        inativos_sairam_hoje=inativos_sairam_hoje,
        detalhe_consultores=detalhe_consultores,
        detalhe_televendas=detalhe_televendas,
    )

    linhas_res = "".join(
        f"<tr><td style='padding:10px; border-top:1px solid #edf2f7;'>{lab}</td><td style='padding:10px; border-top:1px solid #edf2f7; text-align:right'>{int(val)}</td></tr>"
        for lab, val in [
            ("Comprou", resultados.get("comprou", 0)),
            ("Relacionamento", resultados.get("relacionamento", 0)),
            ("Retornar", resultados.get("retornar", 0)),
            ("Sem interesse", resultados.get("sem_interesse", 0)),
            ("Não comprou", resultados.get("nao_comprou", 0)),
        ]
    )

    html = f"""
    <div style="font-family:Arial,Helvetica,sans-serif; font-size:16px; line-height:1.45; color:#222; background:#f7fafc; padding:16px;">
      <h2 style="margin:0 0 12px 0; font-size:28px;">&#128202; Relatório Diário CRM - {hoje.strftime('%d/%m/%Y')}</h2>
      <p style="margin:0 0 18px 0; color:#555; font-size:17px;">Separado por setor, com foco em detalhes por operador.</p>

      <h3 style="margin:0 0 10px 0; font-size:21px;">&#128204; 1) Painel Geral</h3>
      <table cellpadding="10" cellspacing="0" border="0" style="width:100%; border:1px solid #dbe3ee; margin-bottom:18px; font-size:16px; background:#ffffff; border-radius:8px;">
        <tr style="background:#f8fafc;">
          <th align="left">Indicador</th>
          <th align="right">Valor</th>
        </tr>
        <tr><td style='padding:10px; border-top:1px solid #edf2f7;'>Ligações hoje</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right"><b>{_kfmt(total_hoje)}</b></td></tr>
        <tr style='background:#fcfdff;'><td style='padding:10px; border-top:1px solid #edf2f7;'>Ligações ontem</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right"><b>{_kfmt(total_ontem)}</b></td></tr>
        <tr><td style='padding:10px; border-top:1px solid #edf2f7;'>Ligações últimos 7 dias</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right"><b>{_kfmt(total_7)}</b></td></tr>
        <tr style='background:#fcfdff;'><td style='padding:10px; border-top:1px solid #edf2f7;'>Ligações mês atual</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right"><b>{_kfmt(total_mes)}</b></td></tr>
        <tr><td style='padding:10px; border-top:1px solid #edf2f7;'>Conversão mês atual</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right"><b>{conv_mes:.1f}%</b> ({compras_mes}/{total_mes})</td></tr>
        <tr style='background:#fcfdff;'><td style='padding:10px; border-top:1px solid #edf2f7;'>Receita de vendas mês atual</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right"><b>{formatar_dinheiro(receita_mes)}</b></td></tr>
      </table>

      <h3 style="margin:0 0 10px 0; font-size:21px;">&#129302; 1.1) Insights automáticos</h3>
      <div style="border:1px solid #dbe3ee; background:#ffffff; border-radius:8px; padding:12px; margin-bottom:18px;">
        <ul style="margin:0; padding-left:20px; list-style:none;">
          {insights_html}
        </ul>
      </div>

      <h3 style="margin:0 0 10px 0; font-size:21px;">&#128101; 2) Detalhamento de Operadores</h3>
      <div style="font-weight:700; margin:0 0 8px 0; font-size:18px;">&#129489;&#8205;&#128188; Consultores</div>
      <table cellpadding="9" cellspacing="0" border="0" style="width:100%; border:1px solid #dbe3ee; font-size:14px; margin-bottom:16px; background:#ffffff; border-radius:8px;">
        <tr style="background:#f8fafc;">
          <th align="left">Operador</th><th align="right">Hoje</th><th align="right">7d</th><th align="right">Mês</th>
          <th align="right">Relac.</th><th align="right">Comprou</th><th align="right">Não comprou</th><th align="right">Sem interesse</th><th align="right">Retornar</th><th align="right">Meta</th><th align="right">Ating.</th>
        </tr>
        {linhas_consultores or "<tr><td colspan='12' style='color:#64748b; padding:10px;'>Sem dados</td></tr>"}
      </table>

      <div style="font-weight:700; margin:0 0 8px 0; font-size:18px;">&#128222; Televendas</div>
      <table cellpadding="9" cellspacing="0" border="0" style="width:100%; border:1px solid #dbe3ee; font-size:14px; margin-bottom:18px; background:#ffffff; border-radius:8px;">
        <tr style="background:#f8fafc;">
          <th align="left">Operador</th><th align="right">Hoje</th><th align="right">7d</th><th align="right">Mês</th>
          <th align="right">Relac.</th><th align="right">Comprou</th><th align="right">Não comprou</th><th align="right">Sem interesse</th><th align="right">Retornar</th><th align="right">Meta</th><th align="right">Ating.</th>
        </tr>
        {linhas_televendas or "<tr><td colspan='12' style='color:#64748b; padding:10px;'>Sem dados</td></tr>"}
      </table>

      <h3 style="margin:0 0 10px 0; font-size:21px;">&#128450;&#65039; 3) Carteiras Oracle</h3>
      <table cellpadding="10" cellspacing="0" border="0" style="width:100%; border:1px solid #dbe3ee; margin-bottom:18px; font-size:16px; background:#ffffff; border-radius:8px;">
        <tr style="background:#f8fafc;"><th align="left">Indicador</th><th align="right">Valor</th></tr>
        <tr><td style='padding:10px; border-top:1px solid #edf2f7;'>Clientes normais ativos (fora Oracle)</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right"><b>{_kfmt(total_clientes_normais)}</b></td></tr>
        <tr style='background:#fcfdff;'><td style='padding:10px; border-top:1px solid #edf2f7;'>Clientes sem pedidos (90-150 dias)</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right"><b>{_kfmt(total_sem_pedidos_90_150)}</b></td></tr>
        <tr><td style='padding:10px; border-top:1px solid #edf2f7;'>Clientes que irao cair fora no mes (151-180 dias)</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right"><b>{_kfmt(total_proximos_inativacao_151_180)}</b></td></tr>
        <tr><td style='padding:10px; border-top:1px solid #edf2f7;'>Clientes inativos (181-730 dias)</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right"><b>{_kfmt(total_inativos_181_730)}</b></td></tr>
        <tr style='background:#fcfdff;'><td style='padding:10px; border-top:1px solid #edf2f7;'>Movimento inativos hoje</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right">+{inativos_entraram_hoje} / -{inativos_sairam_hoje}</td></tr>
        <tr><td style='padding:10px; border-top:1px solid #edf2f7;'>Última sincronização Oracle</td><td style='padding:10px; border-top:1px solid #edf2f7;' align="right">{sync_txt}</td></tr>
      </table>

      <h3 style="margin:0 0 10px 0; font-size:21px;">&#9989; 4) Qualidade geral (mês atual)</h3>
      <table cellpadding="10" cellspacing="0" border="0" style="width:100%; border:1px solid #dbe3ee; font-size:16px; background:#ffffff; border-radius:8px;">
        <tr style="background:#f8fafc;"><th align="left">Resultado</th><th align="right">Qtde</th></tr>
        {linhas_res or "<tr><td colspan='2' style='color:#64748b; padding:10px;'>Sem dados</td></tr>"}
      </table>
    </div>
    """
    return html


def enviar_relatorio_email(recipients=None):
    recs = recipients or MAIL_RECIPIENTS
    if not recs:
        print("Email: Sem destinatarios")
        return False, "Sem destinatarios configurados."

    if not MAIL_PASSWORD:
        print("Email: Senha nao configurada")
        return False, "MAIL_PASSWORD nao configurado."

    html = build_relatorio_html()
    assunto = _build_assunto_relatorio()

    try:
        print(f"Tentando enviar email para: {', '.join(recs)}")
        msg = Message(subject=assunto, recipients=recs)
        msg.html = html
        mail.send(msg)
        print("Email enviado com sucesso!")
        return True, f"Relatorio enviado para: {', '.join(recs)}"
    except Exception as e:
        print(f"Erro ao enviar email: {e}")
        return False, f"Falha ao enviar e-mail: {e}"

