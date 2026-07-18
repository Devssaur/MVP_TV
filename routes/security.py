import os
from functools import wraps
from typing import Callable, Any

from flask import jsonify, request, g
from supabase import create_client, Client


class AuthzError(Exception):
    pass


def _safe_error(message: str, status: int):
    return jsonify({'erro': message}), status


def _get_public_supabase_client() -> Client:
    url = os.getenv('SUPABASE_URL')
    key = os.getenv('SUPABASE_KEY')
    if not url or not key:
        raise RuntimeError('Variaveis SUPABASE_URL e SUPABASE_KEY nao configuradas.')
    return create_client(url, key)


def _get_service_supabase_client() -> Client:
    url = os.getenv('SUPABASE_URL')
    key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_KEY')
    if not url or not key:
        raise RuntimeError('Variaveis SUPABASE_URL e SUPABASE_SERVICE_KEY nao configuradas.')
    return create_client(url, key)


def _read_bearer_token() -> str:
    auth_header = (request.headers.get('Authorization') or '').strip()
    if auth_header.lower().startswith('bearer '):
        token = auth_header[7:].strip()
        if token:
            return token

    token = (
        request.headers.get('X-Access-Token')
        or request.headers.get('X-Auth-Token')
        or request.headers.get('token')
        or request.args.get('token')
        or request.args.get('access_token')
    )
    if token:
        return str(token).strip()

    raise AuthzError('Token de acesso ausente.')


def get_current_user_context() -> dict[str, Any]:
    if hasattr(g, 'current_user'):
        return g.current_user

    token = _read_bearer_token()
    public_sb = _get_public_supabase_client()
    service_sb = _get_service_supabase_client()

    try:
        user_resp = public_sb.auth.get_user(token)
    except Exception as exc:
        raise AuthzError('Token invalido ou expirado.') from exc
    if not user_resp or not getattr(user_resp, 'user', None):
        raise AuthzError('Token invalido ou expirado.')

    auth_user = user_resp.user
    user_id = auth_user.id

    try:
        perfil_resp = (
            service_sb.table('usuarios')
            .select('id, nome, email, perfil, aprovado, empresa, area')
            .eq('id', user_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        raise AuthzError('Falha ao validar usuario autenticado.') from exc
    perfil_data = perfil_resp.data or {}
    if not perfil_data:
        raise AuthzError('Usuario nao encontrado.')
    if not perfil_data.get('aprovado'):
        raise AuthzError('Acesso pendente. Aguarde aprovacao do administrador.')

    ctx = {
        'id': user_id,
        'email': getattr(auth_user, 'email', None),
        'perfil': perfil_data.get('perfil'),
        'usando_como': perfil_data.get('perfil'),
        'nome': perfil_data.get('nome'),
        'empresa': perfil_data.get('empresa'),
        'area': perfil_data.get('area'),
        'token': token,
    }
    g.current_user = ctx
    return ctx


def require_auth(allowed_profiles: tuple[str, ...] | None = None) -> Callable:
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                user = get_current_user_context()
            except AuthzError as exc:
                message = str(exc)
                if 'pendente' in message.lower():
                    return _safe_error(message, 403)
                return _safe_error(message, 401)
            except Exception:
                return _safe_error('Falha ao validar autenticacao.', 500)

            if allowed_profiles and user.get('perfil') not in allowed_profiles:
                return _safe_error('Acesso negado para este recurso.', 403)
            return fn(*args, **kwargs)

        return wrapper

    return decorator
