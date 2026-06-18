from flask import flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from routes.clientes_ligacoes.access_control import (
    resposta_supervisor_dev_obrigatorio,
    supervisor_dev_liberado,
)
from core.scheduler_runtime import get_scheduler
from services.report_service import enviar_relatorio_email
from services.cranio_insights_snapshot_service import gerar_e_salvar_snapshot_cranio
from services.cranio_ai_summary_service import gerar_e_salvar_resumos_ia_diarios


def register_admin_routes(app):
    @app.route("/admin/enviar-relatorio", methods=["POST", "GET"])
    @login_required
    def admin_enviar_relatorio():
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
        if not supervisor_dev_liberado():
            return resposta_supervisor_dev_obrigatorio()
        ok, msg = enviar_relatorio_email()
        if request.method == "GET":
            flash(msg, "success" if ok else "danger")
            return redirect(url_for("dashboard_supervisor"))
        return jsonify({"ok": ok, "mensagem": msg})

    @app.route("/admin/testar-scheduler")
    @login_required
    def testar_scheduler():
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
        if not supervisor_dev_liberado():
            return resposta_supervisor_dev_obrigatorio()

        try:
            scheduler = get_scheduler()
            if scheduler:
                jobs = scheduler.get_jobs()
                jobs_info = [{"id": job.id, "next_run": str(job.next_run_time), "trigger": str(job.trigger)} for job in jobs]

                return jsonify(
                    {
                        "ok": True,
                        "scheduler_running": scheduler.running,
                        "jobs": jobs_info,
                        "mensagem": "Scheduler está ativo!",
                    }
                )
            return jsonify({"ok": False, "mensagem": "Scheduler não inicializado"})
        except Exception as e:
            return jsonify({"ok": False, "mensagem": str(e)}), 500

    @app.route("/admin/cranio/gerar-snapshot", methods=["POST"])
    @login_required
    def admin_gerar_snapshot_cranio():
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
        if not supervisor_dev_liberado():
            return resposta_supervisor_dev_obrigatorio()
        try:
            payload = gerar_e_salvar_snapshot_cranio()
            return jsonify(
                {
                    "ok": True,
                    "mensagem": "Snapshot do Crânio gerado com sucesso.",
                    "data_ref": (payload or {}).get("data_ref"),
                    "gerado_em": (payload or {}).get("gerado_em"),
                }
            )
        except Exception as e:
            return jsonify({"ok": False, "mensagem": str(e)}), 500

    @app.route("/admin/cranio/gerar-resumo-ia", methods=["POST"])
    @login_required
    def admin_gerar_resumo_ia_cranio():
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
        if not supervisor_dev_liberado():
            return resposta_supervisor_dev_obrigatorio()
        try:
            payload = gerar_e_salvar_resumos_ia_diarios()
            return jsonify(
                {
                    "ok": True,
                    "mensagem": "Resumo estratégico IA do Crânio gerado com sucesso.",
                    "data_ref": (payload or {}).get("data_ref"),
                    "visoes": sorted(list(((payload or {}).get("resumos") or {}).keys())),
                }
            )
        except Exception as e:
            return jsonify({"ok": False, "mensagem": str(e)}), 500
