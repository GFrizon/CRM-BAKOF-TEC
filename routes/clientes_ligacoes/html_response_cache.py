import threading
import time
from collections import OrderedDict

from flask import make_response, request
from flask_login import current_user


_CACHE = OrderedDict()
_LOCK = threading.RLock()
_TTL_SECONDS = 20
_MAX_ENTRIES = 48
_ABAS_CACHEAVEIS = {
    "pendentes",
    "contatados",
    "retornar",
    "ativos",
    "oracle",
    "proximos_inativacao",
    "inativos",
    "construtoras",
}


def limpar_cache_html_listagens():
    with _LOCK:
        _CACHE.clear()


def _requisicao_cacheavel():
    if request.method != "GET" or request.path != "/meus-clientes":
        return False
    if not getattr(current_user, "is_authenticated", False):
        return False
    if request.args.get("nocache") == "1":
        return False
    visao = str(request.args.get("visao") or "").strip().lower()
    if visao != "clientes":
        return False
    aba = str(request.args.get("aba") or "").strip().lower()
    return not aba or aba in _ABAS_CACHEAVEIS


def _chave_cache():
    argumentos = tuple(
        (chave, tuple(sorted(str(valor) for valor in valores)))
        for chave, valores in sorted(request.args.lists())
        if chave != "nocache"
    )
    return (
        int(current_user.id),
        str(getattr(current_user, "tipo", "") or ""),
        bool(getattr(current_user, "viu_novidades", False)),
        request.path,
        argumentos,
    )


def obter_resposta_html_cache():
    if not _requisicao_cacheavel():
        return None

    chave = _chave_cache()
    agora = time.monotonic()
    with _LOCK:
        item = _CACHE.get(chave)
        if not item:
            return None
        if (agora - item["ts"]) > _TTL_SECONDS:
            _CACHE.pop(chave, None)
            return None
        _CACHE.move_to_end(chave)
        corpo = item["body"]
        content_type = item["content_type"]

    response = make_response(corpo, 200)
    response.headers["Content-Type"] = content_type
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-List-Cache"] = "HIT"
    return response


def armazenar_resposta_html_cache(response):
    if not _requisicao_cacheavel():
        return response
    if response.status_code != 200:
        return response
    if response.headers.get("X-List-Cache") == "HIT":
        return response
    if response.headers.getlist("Set-Cookie"):
        response.headers["X-List-Cache"] = "BYPASS"
        return response
    content_type = str(response.headers.get("Content-Type") or "")
    if "text/html" not in content_type.lower():
        return response

    chave = _chave_cache()
    corpo = response.get_data()
    agora = time.monotonic()
    with _LOCK:
        expiradas = [
            cache_key
            for cache_key, item in _CACHE.items()
            if (agora - item["ts"]) > _TTL_SECONDS
        ]
        for cache_key in expiradas:
            _CACHE.pop(cache_key, None)
        _CACHE[chave] = {
            "ts": agora,
            "body": corpo,
            "content_type": content_type,
        }
        _CACHE.move_to_end(chave)
        while len(_CACHE) > _MAX_ENTRIES:
            _CACHE.popitem(last=False)

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-List-Cache"] = "MISS"
    return response


def register_html_response_cache(app):
    @app.before_request
    def _servir_html_listagem_cacheado():
        return obter_resposta_html_cache()

    @app.after_request
    def _armazenar_html_listagem(response):
        return armazenar_resposta_html_cache(response)
