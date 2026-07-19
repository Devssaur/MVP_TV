import os
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request
from supabase import Client, create_client
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError
from routes.security import AuthzError, get_current_user_context

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH, override=True)

try:
    from gotrue.errors import AuthApiError
except Exception:  # pragma: no cover
    AuthApiError = Exception

try:
    from postgrest.exceptions import APIError as PostgrestAPIError
except Exception:  # pragma: no cover
    PostgrestAPIError = Exception

load_dotenv()

auth_bp = Blueprint("auth_bp", __name__)


class CadastroPayload(BaseModel):
    model_config = ConfigDict(extra='ignore')

    nome: str = Field(min_length=2, max_length=120)
    email: EmailStr
    empresa: str = Field(min_length=2, max_length=120)
    area: str = Field(min_length=2, max_length=120)
    senha: str = Field(min_length=8, max_length=72)


class LoginPayload(BaseModel):
    model_config = ConfigDict(extra='ignore')

    email: EmailStr
    senha: str = Field(min_length=1, max_length=72)


class PasswordResetRequestPayload(BaseModel):
    model_config = ConfigDict(extra='ignore')

    email: EmailStr


class PasswordResetConfirmPayload(BaseModel):
    model_config = ConfigDict(extra='ignore')

    token: str = Field(min_length=6, max_length=2048)
    type: str = Field(default='recovery')
    nova_senha: str = Field(min_length=8, max_length=72)


class SwitchModePayload(BaseModel):
    model_config = ConfigDict(extra='ignore')

    modo: str = Field(min_length=3, max_length=32)


def _to_app_profile(db_perfil: str) -> str:
    """Normaliza perfis do banco para os códigos usados no front-end."""
    mapa = {
        "Solicitante": "SOLICITANTE",
        "CCM": "CCM",
        "Administrador": "ADMIN",
        "SIC": "SIC",
    }
    return mapa.get(db_perfil, db_perfil)


def _extract_access_token(resp) -> str | None:
    """Extrai o access token de diferentes formatos de resposta do Supabase."""
    if not resp:
        return None

    if isinstance(resp, dict):
        if resp.get("access_token"):
            return str(resp["access_token"]).strip()
        if resp.get("token"):
            return str(resp["token"]).strip()
        session = resp.get("session")
        if isinstance(session, dict):
            if session.get("access_token"):
                return str(session["access_token"]).strip()
            if session.get("token"):
                return str(session["token"]).strip()
        return None

    for attr_name in ("access_token", "token"):
        value = getattr(resp, attr_name, None)
        if value:
            return str(value).strip()

    session = getattr(resp, "session", None)
    if isinstance(session, dict):
        for attr_name in ("access_token", "token"):
            value = session.get(attr_name)
            if value:
                return str(value).strip()
    else:
        for attr_name in ("access_token", "token"):
            value = getattr(session, attr_name, None)
            if value:
                return str(value).strip()

    return None


def _to_db_profile(app_perfil: str) -> str:
    """Converte códigos do app para os rótulos aceitos na constraint do banco."""
    mapa = {
        "SOLICITANTE": "Solicitante",
        "CCM": "CCM",
        "ADMIN": "Administrador",
        "SIC": "SIC",
    }
    return mapa.get(app_perfil, app_perfil)


def _normalize_app_profile(value: str | None) -> str:
    normalized = (value or '').strip().upper()
    aliases = {
        'ADMINISTRADOR': 'ADMIN',
        'ADMIN': 'ADMIN',
        'SOLICITANTE': 'SOLICITANTE',
        'CCM': 'CCM',
        'SIC': 'SIC',
    }
    return aliases.get(normalized, normalized)


def _allowed_modes_for(base_profile: str) -> list[str]:
    if base_profile == 'ADMIN':
        return ['SOLICITANTE', 'CCM', 'SIC', 'ADMIN']
    if base_profile == 'CCM':
        return ['SOLICITANTE', 'CCM', 'SIC']
    if base_profile == 'SIC':
        return ['SIC']
    return ['SOLICITANTE']


def _get_supabase_client() -> Client:
    supabase_url = (os.getenv("SUPABASE_URL") or "").strip()
    supabase_key = (os.getenv("SUPABASE_KEY") or "").strip()

    if not supabase_url or not supabase_key:
        raise RuntimeError("Configure SUPABASE_URL e SUPABASE_KEY no arquivo .env do projeto.")

    if "SEU_PROJECT_ID" in supabase_url or "SEU" in supabase_url.upper() or "SEU" in supabase_key.upper():
        raise RuntimeError("Substitua os valores de exemplo do arquivo .env pelos dados reais do Supabase.")

    return create_client(supabase_url, supabase_key)


def _get_supabase_service_client() -> Client:
    supabase_url = (os.getenv("SUPABASE_URL") or "").strip()
    supabase_key = (os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY") or "").strip()

    if not supabase_url or not supabase_key:
        raise RuntimeError("Configure SUPABASE_URL e SUPABASE_SERVICE_KEY no arquivo .env do projeto.")

    return create_client(supabase_url, supabase_key)


@auth_bp.route("/debug-usuarios", methods=["GET"])
def debug_usuarios():
    """Rota temporaria para diagnostico. REMOVER antes de ir para producao."""
    if os.getenv('DEV_MODE', '').lower() not in ('1', 'true', 'yes'):
        return jsonify({'erro': 'Rota indisponivel.'}), 404

    try:
        supabase = _get_supabase_client()
        result = supabase.table("usuarios").select(
            "id, nome, email, perfil, aprovado, empresa, area"
        ).execute()
        return jsonify({"usuarios": result.data, "total": len(result.data)}), 200
    except AuthApiError:
        return jsonify({'erro': 'Falha de autenticacao ao consultar usuarios.'}), 401
    except PostgrestAPIError:
        return jsonify({'erro': 'Nao foi possivel consultar os dados.'}), 500
    except Exception:
        return jsonify({'erro': 'Nao foi possivel processar a solicitacao.'}), 500


@auth_bp.route("/cadastro", methods=["POST"])
def cadastro():
    body = request.get_json(silent=True) or {}
    try:
        payload = CadastroPayload.model_validate(body)
    except ValidationError as exc:
        return jsonify({'erro': 'Dados invalidos para cadastro.', 'detalhes': exc.errors()}), 400

    nome = payload.nome.strip()
    email = str(payload.email).strip().lower()
    empresa = payload.empresa.strip()
    area = payload.area.strip()
    senha = payload.senha

    try:
        supabase = _get_supabase_client()
    except RuntimeError:
        return jsonify({"erro": "Configuracao do Supabase ausente."}), 500

    try:
        resp = supabase.auth.sign_up({
            "email":    email,
            "password": senha,
            "options": {
                "data": {
                    "nome":    nome,
                    "perfil":  "Solicitante",
                    "empresa": empresa,
                    "area":    area,
                }
            }
        })
    except AuthApiError as e:
        msg = str(e)
        msg_lower = msg.lower()
        if "already registered" in msg_lower or "already exists" in msg_lower or "user already registered" in msg_lower:
            return jsonify({"erro": "Este e-mail ja esta cadastrado."}), 409
        return jsonify({'erro': 'Nao foi possivel concluir o cadastro. Tente novamente.'}), 500
    except Exception:
        return jsonify({'erro': 'Nao foi possivel concluir o cadastro. Tente novamente.'}), 500

    if resp.user is None:
        # sign_up pode retornar user=None quando e-mail já existe mas confirmação está desabilitada
        return jsonify({"erro": "Este e-mail ja esta cadastrado ou o cadastro foi bloqueado."}), 409

    return jsonify({"mensagem": "Cadastro realizado! Aguarde aprovação do administrador."}), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    try:
        payload = LoginPayload.model_validate(body)
    except ValidationError:
        return jsonify({'erro': 'Credenciais invalidas.'}), 401

    email = str(payload.email).strip().lower()
    senha = payload.senha

    try:
        supabase = _get_supabase_client()
    except RuntimeError:
        return jsonify({"erro": "Configuracao do Supabase ausente."}), 500

    # 1. Autenticar via Supabase Auth
    try:
        resp = supabase.auth.sign_in_with_password({"email": email, "password": senha})
    except AuthApiError:
        return jsonify({"erro": "Credenciais invalidas."}), 401
    except Exception:
        return jsonify({"erro": "Credenciais invalidas."}), 401

    if resp.user is None:
        return jsonify({"erro": "Credenciais inválidas."}), 401

    user_id = resp.user.id

    # 2. Buscar perfil e verificar aprovação
    try:
        result = (
            supabase.table("usuarios")
            .select("id, nome, perfil, usando_como, aprovado, empresa, area")
            .eq("id", user_id)
            .single()
            .execute()
        )
    except PostgrestAPIError:
        return jsonify({'erro': 'Nao foi possivel carregar os dados do usuario.'}), 500
    except Exception:
        return jsonify({"erro": "Nao foi possivel carregar os dados do usuario."}), 500

    usuario = result.data
    if not usuario:
        return jsonify({"erro": "Usuario nao encontrado."}), 404

    if not usuario.get("aprovado"):
        return jsonify({"erro": "Acesso pendente. Aguarde a aprovacao do administrador."}), 403

    usando_como_db = usuario.get("usando_como") or usuario.get("perfil")
    access_token = _extract_access_token(resp)
    return jsonify({
        "id":      usuario["id"],
        "nome":    usuario["nome"],
        "perfil":  _to_app_profile(usuario["perfil"]),
        "usando_como": _to_app_profile(usando_como_db),
        "empresa": usuario.get("empresa"),
        "area":    usuario.get("area"),
        "access_token": access_token,
        "token": access_token,
    }), 200


@auth_bp.route('/modo-visualizacao', methods=['PUT'])
def atualizar_modo_visualizacao():
    try:
        current_user = get_current_user_context()
    except AuthzError as exc:
        message = str(exc)
        if 'pendente' in message.lower():
            return jsonify({'erro': message}), 403
        return jsonify({'erro': message}), 401
    except Exception:
        return jsonify({'erro': 'Falha ao validar autenticacao.'}), 500

    body = request.get_json(silent=True) or {}
    try:
        payload = SwitchModePayload.model_validate(body)
    except ValidationError as exc:
        return jsonify({'erro': 'Modo de visualizacao invalido.', 'detalhes': exc.errors()}), 400

    base_profile = _normalize_app_profile(_to_app_profile(current_user.get('perfil') or ''))
    requested_mode = _normalize_app_profile(payload.modo)
    allowed_modes = _allowed_modes_for(base_profile)
    if requested_mode not in allowed_modes:
        return jsonify({'erro': 'Modo de visualizacao nao permitido para seu perfil.'}), 403

    try:
        supabase = _get_supabase_service_client()
        # usando_como uses app mode codes in DB (SOLICITANTE/CCM/SIC/ADMIN).
        supabase.table('usuarios').update({'usando_como': requested_mode}).eq('id', current_user['id']).execute()
    except PostgrestAPIError:
        return jsonify({'erro': 'Nao foi possivel atualizar sua visualizacao atual.'}), 500
    except Exception:
        return jsonify({'erro': 'Nao foi possivel processar a solicitacao.'}), 500

    return jsonify({'mensagem': 'Visualizacao atualizada.', 'usando_como': requested_mode}), 200


@auth_bp.route('/password-reset/request', methods=['POST'])
def password_reset_request():
    body = request.get_json(silent=True) or {}
    try:
        payload = PasswordResetRequestPayload.model_validate(body)
    except ValidationError as exc:
        return jsonify({'erro': 'Dados invalidos para recuperacao de senha.', 'detalhes': exc.errors()}), 400

    try:
        supabase = _get_supabase_client()
        redirect_to = os.getenv('APP_RESET_REDIRECT_URL') or os.getenv('FRONTEND_URL')
        options = {'redirect_to': redirect_to} if redirect_to else None
        supabase.auth.reset_password_email(str(payload.email), options=options)
        return jsonify({'mensagem': 'Se o e-mail existir, enviaremos instrucoes para redefinir a senha.'}), 200
    except AuthApiError:
        return jsonify({'mensagem': 'Se o e-mail existir, enviaremos instrucoes para redefinir a senha.'}), 200
    except Exception:
        return jsonify({'erro': 'Nao foi possivel processar a solicitacao de recuperacao.'}), 500


@auth_bp.route('/password-reset/confirm', methods=['POST'])
def password_reset_confirm():
    body = request.get_json(silent=True) or {}
    try:
        payload = PasswordResetConfirmPayload.model_validate(body)
    except ValidationError as exc:
        return jsonify({'erro': 'Dados invalidos para redefinicao de senha.', 'detalhes': exc.errors()}), 400

    try:
        supabase = _get_supabase_client()
        supabase.auth.verify_otp({'token': payload.token, 'type': payload.type})
        supabase.auth.update_user({'password': payload.nova_senha})
        return jsonify({'mensagem': 'Senha redefinida com sucesso.'}), 200
    except AuthApiError:
        return jsonify({'erro': 'Token invalido ou expirado.'}), 401
    except Exception:
        return jsonify({'erro': 'Nao foi possivel redefinir a senha.'}), 500
