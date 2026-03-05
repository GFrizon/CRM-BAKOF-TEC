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
                ok, msg = enviar_relatorio_email(mail_recipients)
                print(f"Relatório automático: {msg}")
            except Exception as e:
                print(f"Erro no relatório automático: {e}")

    def job_sincronizacao_oracle():
        """Job diário de sincronização com Oracle"""
        with app.app_context():
            try:
                from sincronizacao_automatica import sincronizacao_automatica_diaria

                print("[INFO] Iniciando sincronizacao automatica com Oracle...")
                sincronizacao_automatica_diaria()
                print("[OK] Sincronizacao automatica concluida com sucesso!")

            except Exception as e:
                print(f"[ERRO] Erro na sincronizacao Oracle: {e}")
                import traceback

                traceback.print_exc()

    _scheduler.add_job(
        job_relatorio,
        trigger="cron",
        day_of_week="mon-fri",
        hour=18,
        minute=0,
        id="relatorio_diario",
        replace_existing=True,
    )

    _scheduler.add_job(
        job_sincronizacao_oracle,
        trigger="cron",
        hour=7,
        minute=20,
        id="sincronizacao_oracle_diaria",
        replace_existing=True,
    )
    _scheduler.start()
    app._scheduler_started = True
    print("Scheduler configurado: envio diário às 18:00 (America/Sao_Paulo)")


def get_scheduler():
    return _scheduler
