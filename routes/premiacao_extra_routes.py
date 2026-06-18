"""
Rotas para a campanha Premiação Extra Mês.
Totalmente separada da Reativação Premiada.
Supervisores: acesso completo + configuração.
Consultores: somente leitura.
"""
from calendar import monthrange
from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from core.extensions import db
from core.models import ConfiguracaoPremiacaoExtra
from routes.clientes_ligacoes.access_control import supervisor_dev_liberado
from oracle_service import get_metas_representantes_oracle, get_vendas_mes_representantes_oracle

_TIPOS_PERMITIDOS = ("supervisor", "consultor")

MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def _formatar_moeda(valor):
    try:
        return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "R$ 0,00"


def _parse_ano_mes(*, ano_raw, mes_raw, ano_padrao, mes_padrao):
    try:
        ano_ref = int(ano_raw or ano_padrao)
        mes_ref = int(mes_raw or mes_padrao)
        if not (1 <= mes_ref <= 12):
            raise ValueError
    except (TypeError, ValueError):
        ano_ref = int(ano_padrao)
        mes_ref = int(mes_padrao)
    return ano_ref, mes_ref


def _query_config_periodo(ano_ref: int, mes_ref: int):
    return ConfiguracaoPremiacaoExtra.query.filter_by(
        ano_ref=int(ano_ref),
        mes_ref=int(mes_ref),
    )


def _get_config_periodo(ano_ref: int, mes_ref: int):
    return _query_config_periodo(ano_ref, mes_ref).order_by(
        ConfiguracaoPremiacaoExtra.id.desc()
    ).first()


def _get_config_mais_recente():
    return (
        ConfiguracaoPremiacaoExtra.query
        .order_by(
            ConfiguracaoPremiacaoExtra.ano_ref.desc(),
            ConfiguracaoPremiacaoExtra.mes_ref.desc(),
            ConfiguracaoPremiacaoExtra.id.desc(),
        )
        .first()
    )


def _montar_config_base(ano_ref: int, mes_ref: int):
    base = _get_config_mais_recente()
    cfg = ConfiguracaoPremiacaoExtra(
        ano_ref=int(ano_ref),
        mes_ref=int(mes_ref),
    )
    if not base:
        return cfg

    cfg.dia_corte_intermediario = base.dia_corte_intermediario
    cfg.pct_meta_intermediaria = base.pct_meta_intermediaria
    cfg.bonus_meta_no_prazo = base.bonus_meta_no_prazo
    cfg.bonus_meta_fim_mes = base.bonus_meta_fim_mes
    cfg.bonus_atendimentos = base.bonus_atendimentos
    cfg.pct_comissao_inativo = base.pct_comissao_inativo
    cfg.min_itens_inativo = base.min_itens_inativo
    cfg.valor_premio_151_180 = base.valor_premio_151_180
    cfg.ativo = base.ativo
    return cfg


def _get_config_efetiva(ano_ref: int, mes_ref: int) -> ConfiguracaoPremiacaoExtra:
    cfg = _get_config_periodo(ano_ref, mes_ref)
    if cfg:
        return cfg
    return _montar_config_base(ano_ref, mes_ref)


def _get_or_create_config_periodo(ano_ref: int, mes_ref: int) -> ConfiguracaoPremiacaoExtra:
    cfg = _get_config_periodo(ano_ref, mes_ref)
    if cfg:
        return cfg

    cfg = _montar_config_base(ano_ref, mes_ref)
    db.session.add(cfg)
    db.session.commit()
    return cfg


def register_premiacao_extra_routes(app):

    @app.route("/campanhas/premiacao-extra")
    @login_required
    def premiacao_extra():
        if current_user.tipo not in _TIPOS_PERMITIDOS:
            flash("Acesso não autorizado.", "danger")
            return redirect(url_for("meus_clientes"))

        eh_supervisor = current_user.tipo == "supervisor"
        pode_configurar = eh_supervisor

        # Parâmetro de mês/ano via query string (padrão: cfg)
        hoje = datetime.now()
        ano_ref, mes_ref = _parse_ano_mes(
            ano_raw=request.args.get("ano"),
            mes_raw=request.args.get("mes"),
            ano_padrao=hoje.year,
            mes_padrao=hoje.month,
        )
        cfg = _get_config_efetiva(ano_ref, mes_ref)

        # Busca vendas mês completo e até dia de corte
        try:
            vendas_mes = get_vendas_mes_representantes_oracle(ano_ref, mes_ref)
        except Exception as e:
            app.logger.error("Erro ao buscar vendas premiacao_extra: %s", e)
            vendas_mes = []

        try:
            dia_corte = int(cfg.dia_corte_intermediario or 22)
            ultimo_dia = monthrange(ano_ref, mes_ref)[1]
            dia_corte_efetivo = min(dia_corte, ultimo_dia)
            vendas_ate_corte = get_vendas_mes_representantes_oracle(
                ano_ref, mes_ref, dia_corte=dia_corte_efetivo
            )
        except Exception as e:
            app.logger.error("Erro ao buscar vendas até corte premiacao_extra: %s", e)
            vendas_ate_corte = []

        try:
            metas = get_metas_representantes_oracle(ano_ref, mes_ref)
        except Exception as e:
            app.logger.error("Erro ao buscar metas premiacao_extra: %s", e)
            metas = []

        # Montar mapa: cd_rep -> meta
        mapa_meta = {}
        for m in metas:
            cd = str(m.get("cd_representante") or "").strip()
            if cd:
                mapa_meta[cd] = float(m.get("meta") or 0)

        # Montar mapa: cd_rep -> venda até corte
        mapa_venda_corte = {}
        for v in vendas_ate_corte:
            cd = str(v.get("cd_representante") or "").strip()
            if cd:
                mapa_venda_corte[cd] = float(v.get("total_vendas") or 0)

        # Montar lista de representantes com métricas
        pct_intermediaria = float(cfg.pct_meta_intermediaria or 75)
        bonus_prazo = float(cfg.bonus_meta_no_prazo or 1)
        bonus_fim = float(cfg.bonus_meta_fim_mes or 0.3)

        representantes = []
        for v in vendas_mes:
            cd = str(v.get("cd_representante") or "").strip()
            nome = str(v.get("nome_representante") or "").strip()
            consultor = str(v.get("categoria_consultor") or "SEM CONSULTOR").strip()
            venda_total = float(v.get("total_vendas") or 0)
            meta = mapa_meta.get(cd, 0)
            venda_corte = mapa_venda_corte.get(cd, 0)

            pct_total = (venda_total / meta * 100) if meta > 0 else None
            pct_corte = (venda_corte / meta * 100) if meta > 0 else None

            atingiu_no_prazo = pct_corte is not None and pct_corte >= pct_intermediaria
            atingiu_fim_mes = pct_total is not None and pct_total >= 100

            bonus_valor = 0.0
            status_meta = "sem_meta"
            if meta > 0:
                if atingiu_no_prazo:
                    bonus_valor = (meta * pct_intermediaria / 100) * (bonus_prazo / 100)
                    status_meta = "prazo"
                elif atingiu_fim_mes:
                    bonus_valor = meta * (bonus_fim / 100)
                    status_meta = "fim_mes"
                else:
                    status_meta = "abaixo"

            representantes.append({
                "cd_representante": cd,
                "nome_representante": nome,
                "categoria_consultor": consultor,
                "meta": meta,
                "venda_total": venda_total,
                "venda_ate_corte": venda_corte,
                "pct_total": round(pct_total, 1) if pct_total is not None else None,
                "pct_ate_corte": round(pct_corte, 1) if pct_corte is not None else None,
                "atingiu_no_prazo": atingiu_no_prazo,
                "atingiu_fim_mes": atingiu_fim_mes,
                "status_meta": status_meta,
                "bonus_valor": bonus_valor,
            })

        # Incluir reps com meta mas sem venda
        cds_com_venda = {r["cd_representante"] for r in representantes}
        for m in metas:
            cd = str(m.get("cd_representante") or "").strip()
            if cd and cd not in cds_com_venda:
                meta = float(m.get("meta") or 0)
                representantes.append({
                    "cd_representante": cd,
                    "nome_representante": cd,
                    "categoria_consultor": "SEM CONSULTOR",
                    "meta": meta,
                    "venda_total": 0.0,
                    "venda_ate_corte": 0.0,
                    "pct_total": 0.0 if meta > 0 else None,
                    "pct_ate_corte": 0.0 if meta > 0 else None,
                    "atingiu_no_prazo": False,
                    "atingiu_fim_mes": False,
                    "status_meta": "abaixo" if meta > 0 else "sem_meta",
                    "bonus_valor": 0.0,
                })

        _ordem_status = {"prazo": 0, "fim_mes": 1, "abaixo": 2, "sem_meta": 3}
        representantes.sort(key=lambda r: (
            _ordem_status.get(r["status_meta"], 9),
            -(r["pct_total"] or 0),
        ))

        total_bonus = sum(r["bonus_valor"] for r in representantes)

        # Agrupar por consultor (ordenado alfabeticamente)
        from collections import defaultdict
        grupos = defaultdict(list)
        for r in representantes:
            grupos[r["categoria_consultor"]].append(r)
        consultores = sorted(grupos.keys())
        grupos_consultores = [
            {
                "consultor": c,
                "representantes": grupos[c],
                "total_bonus": sum(r["bonus_valor"] for r in grupos[c]),
                "qtd_premiados": sum(1 for r in grupos[c] if r["bonus_valor"] > 0),
            }
            for c in consultores
        ]

        return render_template(
            "campanhas/premiacao_extra.html",
            campanha_config=cfg,
            representantes=representantes,
            grupos_consultores=grupos_consultores,
            pode_configurar_premiacao_extra=pode_configurar,
            ano_ref=ano_ref,
            mes_ref=mes_ref,
            mes_ref_nome=MESES_PT.get(mes_ref, str(mes_ref)),
            total_bonus=total_bonus,
            eh_supervisor=eh_supervisor,
            formatar_moeda=_formatar_moeda,
            hoje=hoje.strftime("%Y-%m-%d"),
            dia_corte=int(cfg.dia_corte_intermediario or 22),
        )

    @app.route("/campanhas/premiacao-extra/configuracao", methods=["GET", "POST"])
    @login_required
    def premiacao_extra_configuracao():
        if current_user.tipo != "supervisor":
            flash("Acesso restrito a supervisores.", "danger")
            return redirect(url_for("premiacao_extra"))

        hoje = datetime.now()
        ano_ref, mes_ref = _parse_ano_mes(
            ano_raw=request.values.get("ano_ref") or request.args.get("ano"),
            mes_raw=request.values.get("mes_ref") or request.args.get("mes"),
            ano_padrao=hoje.year,
            mes_padrao=hoje.month,
        )
        cfg = _get_or_create_config_periodo(ano_ref, mes_ref)

        if request.method == "POST":
            try:
                ano_destino, mes_destino = _parse_ano_mes(
                    ano_raw=request.form["ano_ref"],
                    mes_raw=request.form["mes_ref"],
                    ano_padrao=cfg.ano_ref,
                    mes_padrao=cfg.mes_ref,
                )
                cfg_destino = _get_or_create_config_periodo(ano_destino, mes_destino)
                cfg_destino.ano_ref = ano_destino
                cfg_destino.mes_ref = mes_destino
                cfg_destino.dia_corte_intermediario = int(request.form["dia_corte_intermediario"])
                cfg_destino.pct_meta_intermediaria = float(request.form["pct_meta_intermediaria"])
                cfg_destino.bonus_meta_no_prazo = float(request.form["bonus_meta_no_prazo"])
                cfg_destino.bonus_meta_fim_mes = float(request.form["bonus_meta_fim_mes"])
                cfg_destino.atualizado_por_id = current_user.id
                db.session.commit()
                flash("Configurações salvas com sucesso.", "success")
            except (ValueError, KeyError) as e:
                flash(f"Erro ao salvar: {e}", "danger")
                ano_destino, mes_destino = cfg.ano_ref, cfg.mes_ref
            return redirect(url_for("premiacao_extra_configuracao", ano=ano_destino, mes=mes_destino))

        meses = [(n, nome) for n, nome in MESES_PT.items()]
        return render_template(
            "campanhas/premiacao_extra_config.html",
            campanha_config=cfg,
            meses=meses,
        )
