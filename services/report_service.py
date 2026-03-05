from datetime import datetime, timedelta

from flask_mail import Message
from sqlalchemy import case, desc, func, or_

from core.config import MAIL_PASSWORD, MAIL_RECIPIENTS
from core.extensions import db, mail
from core.helpers import _kfmt, _percent, formatar_dinheiro
from core.models import Ligacao, Usuario


def build_relatorio_html():
    hoje = datetime.now().date()
    agora = datetime.now()
    desde7 = agora - timedelta(days=7)
    desde30 = agora - timedelta(days=30)

    total_hoje = (
        db.session.query(func.count(Ligacao.id)).filter(func.date(Ligacao.data_hora) == hoje).scalar()
    ) or 0
    total_7 = db.session.query(func.count(Ligacao.id)).filter(Ligacao.data_hora >= desde7).scalar() or 0
    total_30 = db.session.query(func.count(Ligacao.id)).filter(Ligacao.data_hora >= desde30).scalar() or 0

    resultados = dict(
        (r or "nao_comprou", int(c))
        for r, c in (
            db.session.query(Ligacao.resultado, func.count(Ligacao.id))
            .filter(Ligacao.data_hora >= desde30)
            .group_by(Ligacao.resultado)
            .all()
        )
    )
    compras_30 = resultados.get("comprou", 0)
    conv_30 = _percent(compras_30, total_30)

    ranking = (
        db.session.query(Usuario.nome, func.count(Ligacao.id).label("qtd"))
        .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
        .filter(Usuario.tipo == "consultor", Usuario.ativo == True)
        .filter(or_(Ligacao.data_hora >= desde30, Ligacao.id == None))
        .group_by(Usuario.id, Usuario.nome)
        .order_by(desc("qtd"))
        .all()
    )

    progresso = []
    consultores_ativos = Usuario.query.filter_by(tipo="consultor", ativo=True).order_by(Usuario.nome).all()
    for u in consultores_ativos:
        feitas = (
            db.session.query(func.count(Ligacao.id))
            .filter(Ligacao.consultor_id == u.id)
            .filter(func.date(Ligacao.data_hora) == hoje)
            .scalar()
        ) or 0
        meta = u.meta_diaria or 0
        perc = round(_percent(feitas, meta), 1) if meta else 0.0
        progresso.append((u.nome, feitas, meta, perc))
    progresso.sort(key=lambda x: x[3], reverse=True)

    ult7 = (
        db.session.query(func.date(Ligacao.data_hora), func.count(Ligacao.id))
        .filter(Ligacao.data_hora >= desde7)
        .group_by(func.date(Ligacao.data_hora))
        .order_by(func.date(Ligacao.data_hora))
        .all()
    )

    linhas_ult7 = "".join(
        f"<tr><td>{d.strftime('%d/%m')}</td><td style='text-align:right'>{int(t)}</td></tr>" for d, t in ult7
    )

    max_ult7 = max((int(t) for _, t in ult7), default=0)
    linhas_ult7_graf = ""
    if max_ult7 > 0:
        total_blocos = 30
        for d, t in ult7:
            t_int = int(t)
            blocos_preenchidos = int(round(t_int / max_ult7 * total_blocos))
            blocos_preenchidos = max(0, min(blocos_preenchidos, total_blocos))
            barra = "█" * blocos_preenchidos + "░" * (total_blocos - blocos_preenchidos)
            linhas_ult7_graf += (
                "<tr>"
                f"<td>{d.strftime('%d/%m')}</td>"
                f"<td style='font-family:monospace; white-space:nowrap;'>{barra}</td>"
                f"<td style='text-align:right'>{t_int}</td>"
                "</tr>"
            )

    desempenho_hoje = (
        db.session.query(
            Usuario.id,
            Usuario.nome,
            Usuario.meta_diaria,
            func.count(Ligacao.id).label("ligacoes"),
            func.sum(case((Ligacao.resultado == "comprou", 1), else_=0)).label("vendas"),
            func.sum(case((Ligacao.resultado == "comprou", Ligacao.valor_venda), else_=0)).label("receita"),
        )
        .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
        .filter(Usuario.tipo == "consultor", Usuario.ativo == True)
        .filter(or_(func.date(Ligacao.data_hora) == hoje, Ligacao.id == None))
        .group_by(Usuario.id, Usuario.nome, Usuario.meta_diaria)
        .order_by(Usuario.nome)
        .all()
    )

    desempenho_30 = (
        db.session.query(
            Usuario.id,
            Usuario.nome,
            func.count(Ligacao.id).label("ligacoes"),
            func.sum(case((Ligacao.resultado == "comprou", 1), else_=0)).label("vendas"),
            func.sum(case((Ligacao.resultado == "comprou", Ligacao.valor_venda), else_=0)).label("receita"),
        )
        .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
        .filter(Usuario.tipo == "consultor", Usuario.ativo == True)
        .filter(or_(Ligacao.data_hora >= desde30, Ligacao.id == None))
        .group_by(Usuario.id, Usuario.nome)
        .order_by(Usuario.nome)
        .all()
    )

    linhas_rank = "".join(
        f"<tr><td>{nome}</td><td style='text-align:right'>{int(q or 0)}</td></tr>" for nome, q in ranking
    )

    linhas_prog = "".join(
        f"<tr><td>{nome}</td><td style='text-align:right'>{feitas} / {meta}</td><td style='text-align:right'>{perc:.1f}%</td></tr>"
        for (nome, feitas, meta, perc) in progresso
    )

    linhas_res = "".join(
        f"<tr><td>{lab}</td><td style='text-align:right'>{int(val)}</td></tr>"
        for lab, val in [
            ("Comprou", resultados.get("comprou", 0)),
            ("Rel. (pós-venda)", resultados.get("relacionamento", 0)),
            ("Retornar", resultados.get("retornar", 0)),
            ("Sem interesse", resultados.get("sem_interesse", 0)),
            ("Não comprou", resultados.get("nao_comprou", 0)),
        ]
    )

    linhas_consultor_hoje = ""
    for _id, nome, meta, lig, vend, rec in desempenho_hoje:
        lig = int(lig or 0)
        vend = int(vend or 0)
        rec = float(rec or 0)
        meta = int(meta or 0)
        pct_meta = _percent(lig, meta) if meta else 0.0

        linhas_consultor_hoje += (
            "<tr>"
            f"<td>{nome}</td>"
            f"<td style='text-align:right'>{lig}</td>"
            f"<td style='text-align:right'>{vend}</td>"
            f"<td style='text-align:right'>{formatar_dinheiro(rec)}</td>"
            f"<td style='text-align:right'>{meta}</td>"
            f"<td style='text-align:right'>{pct_meta:.1f}%</td>"
            "</tr>"
        )

    linhas_consultor_30 = ""
    for _id, nome, lig, vend, rec in desempenho_30:
        lig = int(lig or 0)
        vend = int(vend or 0)
        rec = float(rec or 0)
        conv = _percent(vend, lig) if lig else 0.0
        media_dia = lig / 30.0 if lig else 0.0

        total_blocos = 20
        blocos_preenchidos = int(round((conv / 100) * total_blocos))
        blocos_preenchidos = max(0, min(blocos_preenchidos, total_blocos))
        barra = "█" * blocos_preenchidos + "░" * (total_blocos - blocos_preenchidos)

        linhas_consultor_30 += (
            "<tr>"
            f"<td>{nome}</td>"
            f"<td style='text-align:right'>{lig}</td>"
            f"<td style='text-align:right'>{vend}</td>"
            f"<td style='white-space:nowrap;font-family:monospace;font-size:12px'>{barra}</td>"
            f"<td style='text-align:right'>{conv:.1f}%</td>"
            f"<td style='text-align:right'>{formatar_dinheiro(rec)}</td>"
            f"<td style='text-align:right'>{media_dia:.1f}</td>"
            "</tr>"
        )

    html = f"""
    <div style="font-family:Arial,Helvetica,sans-serif; font-size:14px; color:#222;">
      <h2 style="margin:0 0 10px 0;">📊 Relatório de Ligações — {hoje.strftime('%d/%m/%Y')}</h2>
      <p style="margin:0 0 16px 0; color:#555">Resumo do dia, últimos 7 e 30 dias.</p>

      <table cellpadding="0" cellspacing="0" border="0" style="width:100%; margin-bottom:16px">
        <tr>
          <td style="width:33%; background:#f8fafc; padding:12px; border:1px solid #e5e7eb;">
            <div style="font-size:12px; color:#64748b;">Hoje</div>
            <div style="font-size:22px; font-weight:700;">{_kfmt(total_hoje)}</div>
          </td>
          <td style="width:33%; background:#f8fafc; padding:12px; border:1px solid #e5e7eb;">
            <div style="font-size:12px; color:#64748b;">Últimos 7 dias</div>
            <div style="font-size:22px; font-weight:700;">{_kfmt(total_7)}</div>
          </td>
          <td style="width:33%; background:#f8fafc; padding:12px; border:1px solid #e5e7eb;">
            <div style="font-size:12px; color:#64748b;">Últimos 30 dias</div>
            <div style="font-size:22px; font-weight:700;">{_kfmt(total_30)}</div>
          </td>
        </tr>
      </table>
      <table cellpadding="0" cellspacing="0" border="0" style="width:100%; table-layout:fixed;">
        <tr>
          <td style="vertical-align:top; width:50%; padding-right:8px">
            <h3 style="margin:0 0 8px 0;">📈 Gráfico de ligações (7 dias)</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb;">
              <tr style="background:#f1f5f9"><th align="left">Dia</th><th align="left">Gráfico</th><th align="right">Total</th></tr>
              {linhas_ult7_graf or "<tr><td colspan='3' style='color:#64748b'>Sem dados</td></tr>"}
            </table>
            <h3 style="margin:16px 0 8px 0;">📆 Ligações por dia (7d)</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb;">
              <tr style="background:#f1f5f9"><th align="left">Dia</th><th align="right">Total</th></tr>
              {linhas_ult7 or "<tr><td colspan='2' style='color:#64748b'>Sem dados</td></tr>"}
            </table>
            <h3 style="margin:16px 0 8px 0;">🏆 Ranking (30d)</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb;">
              <tr style="background:#f1f5f9"><th align="left">Consultor</th><th align="right">Ligações</th></tr>
              {linhas_rank or "<tr><td colspan='2' style='color:#64748b'>Sem dados</td></tr>"}
            </table>
          </td>
          <td style="vertical-align:top; width:50%; padding-left:8px">
            <h3 style="margin:0 0 8px 0;">🎯 Progresso meta (hoje)</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb;">
              <tr style="background:#f1f5f9"><th align="left">Consultor</th><th align="right">Feitas/Meta</th><th align="right">% Meta</th></tr>
              {linhas_prog or "<tr><td colspan='3' style='color:#64748b'>Sem dados</td></tr>"}
            </table>
            <h3 style="margin:16px 0 8px 0;">🧭 Resultados (30d)</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb;">
              <tr style="background:#f1f5f9"><th align="left">Status</th><th align="right">Qtde</th></tr>
              {linhas_res or "<tr><td colspan='2' style='color:#64748b'>Sem dados</td></tr>"}
            </table>
            <p style="margin-top:12px; color:#64748b; font-size:12px">Conversão (30d): <b>{conv_30:.1f}%</b> — {compras_30} compras de {total_30} ligações.</p>
            <h3 style="margin:16px 0 8px 0;">👤 Desempenho por consultor — Hoje</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb; font-size:12px;">
              <tr style="background:#f1f5f9"><th align="left">Consultor</th><th align="right">Lig.</th><th align="right">Vend.</th><th align="right">Receita</th><th align="right">Meta</th><th align="right">% Meta</th></tr>
              {linhas_consultor_hoje or "<tr><td colspan='6' style='color:#64748b'>Sem dados</td></tr>"}
            </table>
            <h3 style="margin:16px 0 8px 0;">📆 Desempenho por consultor — 30 dias</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb; font-size:12px;">
              <tr style="background:#f1f5f9"><th align="left">Consultor</th><th align="right">Lig.</th><th align="right">Vend.</th><th align="left">Gráfico</th><th align="right">Conv.</th><th align="right">Receita</th><th align="right">Média/dia</th></tr>
              {linhas_consultor_30 or "<tr><td colspan='7' style='color:#64748b'>Sem dados</td></tr>"}
            </table>
          </td>
        </tr>
      </table>
    </div>
    """
    return html


def enviar_relatorio_email(recipients=None):
    recs = recipients or MAIL_RECIPIENTS
    if not recs:
        print("Email: Sem destinatários")
        return False, "Sem destinatários configurados."

    if not MAIL_PASSWORD:
        print("Email: Senha não configurada")
        return False, "MAIL_PASSWORD não configurado."

    html = build_relatorio_html()
    assunto = f"Relatório de Ligações — {datetime.now().strftime('%d/%m/%Y')}"

    try:
        print(f"Tentando enviar email para: {', '.join(recs)}")
        msg = Message(subject=assunto, recipients=recs)
        msg.html = html
        mail.send(msg)
        print("Email enviado com sucesso!")
        return True, f"Relatório enviado para: {', '.join(recs)}"
    except Exception as e:
        print(f"Erro ao enviar email: {e}")
        return False, f"Falha ao enviar e-mail: {e}"
