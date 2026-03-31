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
                sincronizacao_automatica_diaria()
                app.logger.info("[SCHEDULER] Sincronizacao automatica concluida com sucesso.")

            except Exception as e:
                app.logger.exception(f"[SCHEDULER] Erro na sincronizacao Oracle: {e}")

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
    _scheduler.start()
    app._scheduler_started = True
    app.logger.info("Scheduler configurado: relatorio 18:00 e sincronizacao Oracle 07:20 (America/Sao_Paulo)")

    if precisa_sync_startup():
        app.logger.warning("[SCHEDULER] Sync Oracle em atraso detectada. Executando catch-up no startup.")
        job_sincronizacao_oracle()
    else:
        app.logger.info("[SCHEDULER] Sync Oracle do dia ja realizada; catch-up nao necessario.")


def get_scheduler():
    return _scheduler
