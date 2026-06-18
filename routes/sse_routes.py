from __future__ import annotations

import queue

from flask import Blueprint, Response, stream_with_context
from flask_login import current_user, login_required

from services.sse_bus import cancelar, subscrever

sse_bp = Blueprint("sse", __name__)

_HEARTBEAT = "data: {\"evento\": \"ping\"}\n\n"
_TIMEOUT_BLOCK = 25


@sse_bp.get("/eventos/stream")
@login_required
def stream_eventos():
    user_id = int(current_user.id)

    @stream_with_context
    def _gerar():
        q = subscrever(user_id)
        try:
            yield _HEARTBEAT
            while True:
                try:
                    mensagem = q.get(timeout=_TIMEOUT_BLOCK)
                    if mensagem is None:
                        break
                    yield mensagem
                except queue.Empty:
                    yield _HEARTBEAT
        finally:
            cancelar(user_id, q)

    return Response(
        _gerar(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def register_sse_routes(app):
    app.register_blueprint(sse_bp)
