from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

_scheduler = None


def start_scheduler_once(app, enviar_relatorio_email, mail_recipients):
    from pytz import timezone

    global _scheduler

    if getattr(app, "_scheduler_started", False):
        return

    tz = timezone("America/Sao_Paulo")
    _scheduler = BackgroundScheduler(timezone=tz)

    def job_relatorio():
        with app.app_context():
            try:
                hoje = datetime.now(tz)
                if hoje.weekday() >= 5:  # 5=sabado, 6=domingo
                    app.logger.info("[SCHEDULER] Relatorio nao enviado: fim de semana.")
                    return
                ok, msg = enviar_relatorio_email(mail_recipients)
                app.logger.info(f"Relatorio automatico: {msg}")
            except Exception as e:
                app.logger.exception(f"Erro no relatorio automatico: {e}")

    def job_sincronizacao_oracle():
        """Job diario de sincronizacao com Oracle."""
        with app.app_context():
            try:
                from sincronizacao_automatica import sincronizacao_automatica_diaria

                app.logger.info("[SCHEDULER] Iniciando sincronizacao automatica com Oracle...")
                sincronizacao_automatica_diaria(app)
                app.logger.info("[SCHEDULER] Sincronizacao automatica concluida com sucesso.")

            except Exception as e:
                app.logger.exception(f"[SCHEDULER] Erro na sincronizacao Oracle: {e}")

    def job_cranio_insights_snapshot():
        with app.app_context():
            try:
                from services.cranio_insights_snapshot_service import gerar_e_salvar_snapshot_cranio

                app.logger.info("[SCHEDULER] Gerando snapshot diario do Cranio...")
                payload = gerar_e_salvar_snapshot_cranio()
                app.logger.info(
                    "[SCHEDULER] Snapshot diario do Cranio salvo para %s.",
                    (payload or {}).get("data_ref"),
                )
            except Exception as e:
                app.logger.exception(f"[SCHEDULER] Erro ao gerar snapshot do Cranio: {e}")

    def job_cranio_ai_summary():
        with app.app_context():
            if not app.config.get("CRANIO_AI_SUMMARY_ENABLED"):
                app.logger.info("[SCHEDULER] Resumo IA do Cranio desabilitado por configuracao.")
                return
            if not app.config.get("CRANIO_AI_SUMMARY_AUTO_DAILY"):
                app.logger.info("[SCHEDULER] Resumo IA diario do Cranio desabilitado por configuracao.")
                return
            try:
                from services.cranio_ai_summary_service import gerar_e_salvar_resumos_ia_diarios

                app.logger.info("[SCHEDULER] Gerando resumo estrategico IA do Cranio...")
                payload = gerar_e_salvar_resumos_ia_diarios()
                app.logger.info(
                    "[SCHEDULER] Resumo estrategico IA do Cranio salvo para %s.",
                    (payload or {}).get("data_ref"),
                )
            except Exception as e:
                app.logger.exception(f"[SCHEDULER] Erro ao gerar resumo IA do Cranio: {e}")

    def precisa_sync_startup():
        """Executa catch-up da sync quando o app sobe apos o horario do job."""
        try:
            from core.extensions import db
            from core.models import Cliente
            from sqlalchemy import func

            with app.app_context():
                ultima_sync = (
                    db.session.query(func.max(Cliente.data_ultima_sincronizacao))
                    .filter(Cliente.cd_cliente_oracle.isnot(None))
                    .scalar()
                )
                hoje = datetime.now(tz).date()
                if ultima_sync is None:
                    return True
                return ultima_sync.date() < hoje
        except Exception as e:
            app.logger.exception(f"[SCHEDULER] Falha ao validar ultima sync: {e}")
            return False

    def precisa_cranio_snapshot_startup():
        try:
            from services.cranio_insights_snapshot_service import carregar_snapshot_cranio_insights

            with app.app_context():
                return not bool(carregar_snapshot_cranio_insights())
        except Exception as e:
            app.logger.exception(f"[SCHEDULER] Falha ao validar snapshot do Cranio: {e}")
            return False

    _scheduler.add_job(
        job_relatorio,
        trigger="cron",
        day_of_week="mon-fri",
        hour=18,
        minute=0,
        id="relatorio_diario",
        replace_existing=True,
        misfire_grace_time=60 * 60,
    )

    _scheduler.add_job(
        job_sincronizacao_oracle,
        trigger="cron",
        hour=7,
        minute=20,
        id="sincronizacao_oracle_diaria",
        replace_existing=True,
        misfire_grace_time=60 * 60 * 12,
    )
    _scheduler.add_job(
        job_cranio_insights_snapshot,
        trigger="cron",
        day_of_week="mon-fri",
        hour=7,
        minute=50,
        id="cranio_insights_snapshot_diario",
        replace_existing=True,
        misfire_grace_time=60 * 60 * 12,
    )
    _scheduler.add_job(
        job_cranio_ai_summary,
        trigger="cron",
        day_of_week="mon-fri",
        hour=19,
        minute=10,
        id="cranio_ai_summary_diario",
        replace_existing=True,
        misfire_grace_time=60 * 60 * 12,
    )
    _scheduler.start()
    app._scheduler_started = True
    app.logger.info("Scheduler configurado: relatorio 18:00 e sincronizacao Oracle 07:20 (America/Sao_Paulo)")

    if precisa_sync_startup():
        app.logger.warning("[SCHEDULER] Sync Oracle em atraso detectada. Executando catch-up no startup.")
        job_sincronizacao_oracle()
    else:
        app.logger.info("[SCHEDULER] Sync Oracle do dia ja realizada; catch-up nao necessario.")

    if precisa_cranio_snapshot_startup():
        app.logger.warning("[SCHEDULER] Snapshot do Cranio em atraso detectado. Executando catch-up no startup.")
        job_cranio_insights_snapshot()
    else:
        app.logger.info("[SCHEDULER] Snapshot do Cranio do dia ja existe; catch-up nao necessario.")


def get_scheduler():
    return _scheduler
