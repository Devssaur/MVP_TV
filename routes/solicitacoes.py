import os
import base64
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request, current_app
from supabase import Client, create_client
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from routes.security import require_auth, get_current_user_context

try:
    from gotrue.errors import AuthApiError
except Exception:  # pragma: no cover
    AuthApiError = Exception

try:
    from postgrest.exceptions import APIError as PostgrestAPIError
except Exception:  # pragma: no cover
    PostgrestAPIError = Exception

load_dotenv()

solicitacoes_bp = Blueprint("solicitacoes_bp", __name__)


class CriarSafPayload(BaseModel):
    model_config = ConfigDict(extra='ignore')

    notificador_id: str = Field(min_length=6, max_length=64)
    titulo_falha: str = Field(min_length=3, max_length=200)
    descricao_longa: str | None = Field(default=None, max_length=4000)
    local_instalacao_id: str = Field(min_length=1, max_length=100)
    local_instalacao: str | None = Field(default=None, max_length=200)
    equipamento_id: str | None = Field(default=None, max_length=100)
    equipamento: str | None = Field(default=None, max_length=200)
    sintoma_id: str | None = Field(default=None, max_length=100)
    data_inicio_avaria: str = Field(min_length=8, max_length=20)
    hora_inicio_avaria: str = Field(min_length=4, max_length=20)
    prioridade: str | None = Field(default='ALTA', max_length=20)
    via_numero: str | None = Field(default=None, max_length=3)
    km_inicial: str | None = Field(default=None, max_length=5)
    km_final: str | None = Field(default=None, max_length=5)
    notificador_nome: str | None = Field(default=None, max_length=120)
    notificador_area: str | None = Field(default=None, max_length=120)
    foto_base64: str | None = None


class AtualizarSafPayload(BaseModel):
    model_config = ConfigDict(extra='ignore')

    titulo_falha: str | None = Field(default=None, min_length=3, max_length=200)
    descricao_longa: str | None = Field(default=None, max_length=4000)
    local_instalacao_id: str | None = Field(default=None, min_length=1, max_length=100)
    local_instalacao: str | None = Field(default=None, max_length=200)
    equipamento_id: str | None = Field(default=None, max_length=100)
    equipamento: str | None = Field(default=None, max_length=200)
    sintoma_id: str | None = Field(default=None, max_length=100)
    data_inicio_avaria: str | None = Field(default=None, min_length=8, max_length=20)
    hora_inicio_avaria: str | None = Field(default=None, min_length=4, max_length=20)


class CancelarSafPayload(BaseModel):
    model_config = ConfigDict(extra='ignore')

    motivo_cancelamento: str = Field(min_length=3, max_length=1000)


def _erro_interno(msg: str = 'Nao foi possivel processar sua solicitacao.'):
    return jsonify({'erro': msg}), 500


def _get_supabase_client() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    # Usa service_role para bypass de RLS (storage upload, inserts protegidos)
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        raise RuntimeError("Variaveis SUPABASE_URL e SUPABASE_SERVICE_KEY nao configuradas.")

    return create_client(supabase_url, supabase_key)


@solicitacoes_bp.route("/minhas/<notificador_id>", methods=["GET"])
@require_auth(("Solicitante", "CCM", "Administrador", "SIC"))
def listar_minhas_solicitacoes(notificador_id):
    user = get_current_user_context()
    if user.get('perfil') == 'Solicitante' and user.get('id') != notificador_id:
        return jsonify({'erro': 'Acesso negado para este recurso.'}), 403

    try:
        supabase = _get_supabase_client()
    except RuntimeError:
        return jsonify({"erro": "Configuracao do Supabase ausente"}), 500

    try:
        result = (
            supabase.table("saf_solicitacoes")
            .select(
                "id, titulo_falha, descricao_longa, prioridade, criado_em, status"
            )
            .eq("notificador_id", notificador_id)
            .order("criado_em", desc=True)
            .execute()
        )
        return jsonify({"solicitacoes": result.data, "total": len(result.data)}), 200
    except (AuthApiError, PostgrestAPIError):
        current_app.logger.exception('Erro Supabase ao listar solicitacoes do usuario=%s', notificador_id)
        return _erro_interno('Nao foi possivel consultar suas solicitacoes.')
    except Exception:
        current_app.logger.exception('Erro interno ao listar solicitacoes do usuario=%s', notificador_id)
        return _erro_interno()


@solicitacoes_bp.route("/minhassafs/<usuario_id>", methods=["GET"])
@require_auth(("Solicitante", "CCM", "Administrador", "SIC"))
def listar_minhas_safs(usuario_id):
    user = get_current_user_context()
    if user.get('perfil') == 'Solicitante' and user.get('id') != usuario_id:
        return jsonify({'erro': 'Acesso negado para este recurso.'}), 403

    try:
        supabase = _get_supabase_client()
    except RuntimeError:
        return jsonify({"erro": "Configuracao do Supabase ausente"}), 500

    try:
        result = (
            supabase.table("saf_solicitacoes")
            .select(
                "id, ticket_saf, titulo_falha, descricao_longa, prioridade, anexo_evidencia_url, criado_em, "
                "status, motivo_devolucao, data_avaliacao, tipo_nota, qmnum_duplicata, "
                "saf_integracao_sap(qmnum, aufnr, numero_ordem_sap, tipo_nota, status_integracao)"
            )
            .eq("notificador_id", usuario_id)
            .order("criado_em", desc=True)
            .execute()
        )

        return jsonify({"solicitacoes": result.data, "total": len(result.data)}), 200

    except (AuthApiError, PostgrestAPIError):
        current_app.logger.exception('Erro Supabase ao listar SAFs do usuario=%s', usuario_id)
        return _erro_interno('Nao foi possivel consultar suas SAFs.')
    except Exception:
        current_app.logger.exception('Erro interno ao listar SAFs do usuario=%s', usuario_id)
        return _erro_interno()


@solicitacoes_bp.route("/sic/notificacoes", methods=["GET"])
@require_auth(("SIC", "CCM", "Administrador"))
def listar_notificacoes_sic():
    try:
        supabase = _get_supabase_client()
    except RuntimeError:
        return jsonify({"erro": "Configuracao do Supabase ausente"}), 500

    try:
        result = (
            supabase.table("saf_solicitacoes")
            .select(
                "id, ticket_saf, titulo_falha, descricao_longa, prioridade, criado_em, "
                "status, local_instalacao, equipamento, notificador_nome, notificador_area, "
                "saf_integracao_sap(qmnum, aufnr, numero_ordem_sap, tipo_nota, status_integracao)"
            )
            .order("criado_em", desc=True)
            .execute()
        )

        return jsonify({"solicitacoes": result.data, "total": len(result.data)}), 200
    except (AuthApiError, PostgrestAPIError):
        current_app.logger.exception('Erro Supabase ao listar notificacoes SIC')
        return _erro_interno('Nao foi possivel consultar as notificacoes.')
    except Exception:
        current_app.logger.exception('Erro interno ao listar notificacoes SIC')
        return _erro_interno()


@solicitacoes_bp.route('/<saf_id>', methods=['GET'])
@require_auth(('Solicitante', 'CCM', 'Administrador', 'SIC'))
def buscar_saf(saf_id: str):
    user = get_current_user_context()
    try:
        supabase = _get_supabase_client()
    except RuntimeError:
        return jsonify({'erro': 'Configuracao do Supabase ausente'}), 500

    try:
        resp = (
            supabase.table('saf_solicitacoes')
            .select(
                'id, ticket_saf, titulo_falha, descricao_longa, prioridade, criado_em, '
                'status, motivo_devolucao, motivo_cancelamento, data_avaliacao, '
                'local_instalacao, local_instalacao_id, equipamento, equipamento_id, '
                'sintoma_id, data_inicio_avaria, hora_inicio_avaria, notificador_id, '
                'notificador_nome, notificador_area, anexo_evidencia_url, '
                'saf_integracao_sap(qmnum, aufnr, numero_ordem_sap, tipo_nota, status_integracao, ultima_tentativa_em, mensagem_erro)'
            )
            .eq('id', saf_id)
            .maybe_single()
            .execute()
        )
        saf = resp.data
        if not saf:
            return jsonify({'erro': 'SAF nao encontrada.'}), 404

        if user.get('perfil') == 'Solicitante' and user.get('id') != saf.get('notificador_id'):
            return jsonify({'erro': 'Acesso negado para este recurso.'}), 403

        return jsonify({'solicitacao': saf}), 200
    except (AuthApiError, PostgrestAPIError):
        current_app.logger.exception('Erro Supabase ao buscar SAF id=%s', saf_id)
        return _erro_interno('Nao foi possivel consultar a SAF.')
    except Exception:
        current_app.logger.exception('Erro interno ao buscar SAF id=%s', saf_id)
        return _erro_interno()


@solicitacoes_bp.route('/<saf_id>', methods=['PUT'])
@require_auth(('Solicitante', 'CCM', 'Administrador'))
def atualizar_saf(saf_id: str):
    dados = request.get_json(silent=True) or {}
    try:
        payload = AtualizarSafPayload.model_validate(dados)
    except ValidationError as exc:
        return jsonify({'erro': 'Dados invalidos para atualizacao da SAF.', 'detalhes': exc.errors()}), 400

    campos = payload.model_dump(exclude_none=True)
    if not campos:
        return jsonify({'erro': 'Nenhum campo valido informado para atualizacao.'}), 400

    user = get_current_user_context()
    try:
        supabase = _get_supabase_client()
    except RuntimeError:
        return jsonify({'erro': 'Configuracao do Supabase ausente'}), 500

    try:
        atual = (
            supabase.table('saf_solicitacoes')
            .select('id, notificador_id, status')
            .eq('id', saf_id)
            .maybe_single()
            .execute()
        ).data
        if not atual:
            return jsonify({'erro': 'SAF nao encontrada.'}), 404

        if user.get('perfil') == 'Solicitante' and user.get('id') != atual.get('notificador_id'):
            return jsonify({'erro': 'Acesso negado para este recurso.'}), 403

        if atual.get('status') != 'DEVOLVIDA' and user.get('perfil') == 'Solicitante':
            return jsonify({'erro': 'Apenas SAFs devolvidas podem ser editadas pelo solicitante.'}), 400

        campos['status'] = 'ABERTA'
        campos['motivo_devolucao'] = None
        campos['atualizado_em'] = datetime.now(timezone.utc).isoformat()

        (
            supabase.table('saf_solicitacoes')
            .update(campos)
            .eq('id', saf_id)
            .execute()
        )

        return jsonify({'mensagem': 'SAF atualizada com sucesso.'}), 200
    except (AuthApiError, PostgrestAPIError):
        current_app.logger.exception('Erro Supabase ao atualizar SAF id=%s', saf_id)
        return _erro_interno('Nao foi possivel atualizar a SAF.')
    except Exception:
        current_app.logger.exception('Erro interno ao atualizar SAF id=%s', saf_id)
        return _erro_interno()


@solicitacoes_bp.route('/cancelar/<saf_id>', methods=['PUT'])
@require_auth(('Solicitante', 'CCM', 'Administrador'))
def cancelar_saf(saf_id: str):
    dados = request.get_json(silent=True) or {}
    try:
        payload = CancelarSafPayload.model_validate(dados)
    except ValidationError as exc:
        return jsonify({'erro': 'Dados invalidos para cancelamento da SAF.', 'detalhes': exc.errors()}), 400

    user = get_current_user_context()
    try:
        supabase = _get_supabase_client()
    except RuntimeError:
        return jsonify({'erro': 'Configuracao do Supabase ausente'}), 500

    try:
        resp = (
            supabase.table('saf_solicitacoes')
            .select('id, notificador_id, status, saf_integracao_sap(aufnr, numero_ordem_sap)')
            .eq('id', saf_id)
            .maybe_single()
            .execute()
        )
        saf = resp.data
        if not saf:
            return jsonify({'erro': 'SAF nao encontrada.'}), 404

        if user.get('perfil') == 'Solicitante' and user.get('id') != saf.get('notificador_id'):
            return jsonify({'erro': 'Acesso negado para este recurso.'}), 403

        if saf.get('status') in ('CANCELADA', 'APROVADA'):
            return jsonify({'erro': 'Esta SAF nao pode ser cancelada no status atual.'}), 400

        integracao = (saf.get('saf_integracao_sap') or [])
        integ = integracao[0] if integracao else {}
        if integ.get('aufnr') or integ.get('numero_ordem_sap'):
            return jsonify({'erro': 'Cancelamento bloqueado: Ordem SAP ja vinculada.'}), 400

        (
            supabase.table('saf_solicitacoes')
            .update({
                'status': 'CANCELADA',
                'motivo_cancelamento': payload.motivo_cancelamento.strip(),
                'data_avaliacao': datetime.now(timezone.utc).isoformat(),
            })
            .eq('id', saf_id)
            .execute()
        )

        return jsonify({'mensagem': 'SAF cancelada com sucesso.'}), 200
    except (AuthApiError, PostgrestAPIError):
        current_app.logger.exception('Erro Supabase ao cancelar SAF id=%s', saf_id)
        return _erro_interno('Nao foi possivel cancelar a SAF.')
    except Exception:
        current_app.logger.exception('Erro interno ao cancelar SAF id=%s', saf_id)
        return _erro_interno()


@solicitacoes_bp.route("/criar", methods=["POST"])
@require_auth(("Solicitante", "CCM", "Administrador"))
def criar_saf():
    raw_data = request.get_json(silent=True) or {}
    try:
        payload = CriarSafPayload.model_validate(raw_data)
    except ValidationError as exc:
        return jsonify({'erro': 'Dados invalidos para criacao da SAF.', 'detalhes': exc.errors()}), 400

    dados = payload.model_dump(exclude_none=True)
    user = get_current_user_context()
    if user.get('perfil') == 'Solicitante' and payload.notificador_id != user.get('id'):
        return jsonify({'erro': 'Acesso negado para criar SAF em nome de outro usuario.'}), 403

    request_id = str(uuid.uuid4())

    # Evita logar base64 completo da foto e reduz risco de poluir o terminal.
    dados_log = dict(dados)
    foto_raw = dados_log.get("foto_base64")
    if foto_raw:
        dados_log["foto_base64"] = f"<base64:{len(foto_raw)} chars>"

    current_app.logger.info(
        "[CRIAR_SAF][%s] payload recebido: %s",
        request_id,
        dados_log,
    )

    campos_obrigatorios = [
        "notificador_id",
        "titulo_falha",
        "local_instalacao_id",
        "data_inicio_avaria",
        "hora_inicio_avaria",
    ]
    campos_faltando = [campo for campo in campos_obrigatorios if not dados.get(campo)]
    # descricao_longa é obrigatória apenas se não houver sintoma_id selecionado
    if not dados.get("descricao_longa") and not dados.get("sintoma_id"):
        campos_faltando.append("descricao_longa (ou sintoma_id)")
    if campos_faltando:
        current_app.logger.warning(
            "[CRIAR_SAF][%s] validacao falhou: campos faltando = %s",
            request_id,
            campos_faltando,
        )
        return (
            jsonify(
                {
                    "erro": f"Campos obrigatorios ausentes: {', '.join(campos_faltando)}",
                    "request_id": request_id,
                }
            ),
            400,
        )

    prioridade = str(dados.get("prioridade") or "ALTA").upper().strip()
    if prioridade not in {"BAIXA", "MEDIA", "ALTA", "CRITICA"}:
        prioridade = "ALTA"

    try:
        supabase = _get_supabase_client()
    except RuntimeError:
        current_app.logger.exception(
            "[CRIAR_SAF][%s] falha ao obter cliente Supabase",
            request_id,
        )
        return jsonify({"erro": "Configuracao do Supabase ausente", "request_id": request_id}), 500

    # Evita tentativa inicial inválida que consome sequência de ticket_saf.
    # Se sintoma_id não existir em sintomas_catalogo, grava como nulo.
    sintoma_id_validado = dados.get("sintoma_id")
    if sintoma_id_validado:
        try:
            sintoma_check = (
                supabase.table("sintomas_catalogo")
                .select("id")
                .eq("id", sintoma_id_validado)
                .limit(1)
                .execute()
            )
            if not (sintoma_check.data or []):
                current_app.logger.warning(
                    "[CRIAR_SAF][%s] sintoma_id=%s não encontrado em sintomas_catalogo; salvando com sintoma_id nulo",
                    request_id,
                    sintoma_id_validado,
                )
                sintoma_id_validado = None
        except Exception:
            current_app.logger.exception(
                "[CRIAR_SAF][%s] falha ao validar sintoma_id em sintomas_catalogo; salvando com sintoma_id nulo",
                request_id,
            )
            sintoma_id_validado = None

    try:
        # 1. Monta o payload com os novos nomes de colunas
        nova_saf = {
            "notificador_id":      dados.get("notificador_id"),
            "notificador_nome":    dados.get("notificador_nome"),
            "notificador_area":    dados.get("notificador_area"),
            "titulo_falha":        dados.get("titulo_falha"),
            "descricao_longa":     dados.get("descricao_longa"),
            "local_instalacao":    dados.get("local_instalacao"),
            "local_instalacao_id": dados.get("local_instalacao_id"),
            "equipamento":         dados.get("equipamento"),
            "equipamento_id":      dados.get("equipamento_id"),
            "sintoma_id":          sintoma_id_validado,
            "prioridade":          prioridade,
            "via_numero":          dados.get("via_numero"),
            "km_inicial":          dados.get("km_inicial"),
            "km_final":            dados.get("km_final"),
            "data_inicio_avaria":  dados.get("data_inicio_avaria"),
            "hora_inicio_avaria":  dados.get("hora_inicio_avaria"),
        }

        current_app.logger.info(
            "[CRIAR_SAF][%s] etapa=insert_saf payload=%s",
            request_id,
            nova_saf,
        )

        # 2. Insere na tabela principal
        # Compatibilidade com bancos legados:
        # - sem coluna sintoma_id
        # - prioridade armazenada como inteiro (1..4)
        insert_payload = dict(nova_saf)
        prioridade_legacy = {
            "BAIXA": 1,
            "MEDIA": 2,
            "ALTA": 3,
            "CRITICA": 4,
        }

        for tentativa in range(3):
            try:
                resposta_saf = supabase.table("saf_solicitacoes").insert(insert_payload).execute()
                break
            except Exception as insert_err:
                err_txt = str(insert_err)
                handled = False

                if (
                    "Could not find the 'sintoma_id' column" in err_txt
                    or ("sintoma_id" in err_txt and "schema cache" in err_txt)
                ) and "sintoma_id" in insert_payload:
                    current_app.logger.warning(
                        "[CRIAR_SAF][%s] tentativa=%s coluna sintoma_id ausente; retry sem sintoma_id",
                        request_id,
                        tentativa + 1,
                    )
                    insert_payload.pop("sintoma_id", None)
                    handled = True

                # Banco atual referencia saf_solicitacoes.sintoma_id -> sintomas_catalogo(id).
                # Quando o front envia ID vindo da tabela sintomas, evita quebrar a criacao da SAF.
                if (
                    "saf_solicitacoes_sintoma_id_fkey" in err_txt
                    or (
                        "violates foreign key constraint" in err_txt
                        and "sintoma_id" in err_txt
                        and "sintomas_catalogo" in err_txt
                    )
                ) and "sintoma_id" in insert_payload:
                    current_app.logger.warning(
                        "[CRIAR_SAF][%s] tentativa=%s sintoma_id sem correspondencia em sintomas_catalogo; retry com sintoma_id nulo",
                        request_id,
                        tentativa + 1,
                    )
                    insert_payload["sintoma_id"] = None
                    handled = True

                if (
                    "invalid input syntax for type integer" in err_txt
                    and any(x in err_txt for x in ("MEDIA", "BAIXA", "ALTA", "CRITICA"))
                    and isinstance(insert_payload.get("prioridade"), str)
                ):
                    current_app.logger.warning(
                        "[CRIAR_SAF][%s] tentativa=%s prioridade em texto nao aceita; retry com mapeamento inteiro",
                        request_id,
                        tentativa + 1,
                    )
                    insert_payload["prioridade"] = prioridade_legacy.get(prioridade, 2)
                    handled = True

                if not handled:
                    raise
        else:
            raise RuntimeError("Falha ao inserir SAF apos tentativas de compatibilidade.")

        if not resposta_saf.data:
            raise RuntimeError(
                f"Insercao retornou sem dados. resposta={resposta_saf}"
            )

        saf_id = resposta_saf.data[0]["id"]
        ticket = resposta_saf.data[0]["ticket_saf"]

        current_app.logger.info(
            "[CRIAR_SAF][%s] etapa=insert_saf ok id=%s ticket=%s",
            request_id,
            saf_id,
            ticket,
        )

        # 3. Upload da foto de evidência para o Storage
        foto_b64 = dados.get("foto_base64") or ""
        if foto_b64:
            if "," in foto_b64:
                foto_b64 = foto_b64.split(",", 1)[1]
            try:
                current_app.logger.info(
                    "[CRIAR_SAF][%s] etapa=upload_foto inicio (%s chars)",
                    request_id,
                    len(foto_b64),
                )
                img_bytes = base64.b64decode(foto_b64)
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                storage_path = f"safs/{saf_id}/{ts}_evidencia.jpg"
                supabase.storage.from_("saf-evidencias").upload(
                    path=storage_path,
                    file=img_bytes,
                    file_options={"content-type": "image/jpeg", "upsert": "true"},
                )
                public_url = supabase.storage.from_("saf-evidencias").get_public_url(storage_path)
                supabase.table("saf_solicitacoes").update(
                    {"anexo_evidencia_url": public_url}
                ).eq("id", saf_id).execute()
                current_app.logger.info(
                    "[CRIAR_SAF][%s] etapa=upload_foto ok path=%s",
                    request_id,
                    storage_path,
                )
            except Exception:
                current_app.logger.exception(
                    "[CRIAR_SAF][%s] etapa=upload_foto falhou (nao bloqueante)",
                    request_id,
                )
                # Falha no upload não deve bloquear a criação da SAF

        # 4. Registra auditoria (best-effort para nao quebrar a criacao da SAF)
        try:
            supabase.table("logs_auditoria").insert(
                {
                    "usuario_id": dados.get("notificador_id"),
                    "evento": "CRIACAO_SAF",
                    "payload": {
                        "entidade": "saf_solicitacoes",
                        "entidade_id": str(saf_id),
                        "ticket_saf": ticket,
                        "dados_enviados": nova_saf,
                    },
                }
            ).execute()
        except Exception:
            current_app.logger.exception(
                "[CRIAR_SAF][%s] etapa=auditoria falhou (nao bloqueante)",
                request_id,
            )

        current_app.logger.info("[CRIAR_SAF][%s] concluido com sucesso", request_id)

        return (
            jsonify(
                {
                    "mensagem": "SAF criada com sucesso!",
                    "ticket": f"SAF #{str(ticket).zfill(6)}",
                    "id": saf_id,
                    "request_id": request_id,
                }
            ),
            201,
        )

    except (AuthApiError, PostgrestAPIError):
        current_app.logger.exception('[CRIAR_SAF][%s] erro de dados/autenticacao Supabase', request_id)
        return jsonify({'erro': 'Nao foi possivel salvar seus dados. Tente novamente.', 'request_id': request_id}), 500
    except Exception:
        current_app.logger.exception('[CRIAR_SAF][%s] erro interno', request_id)
        return jsonify({'erro': 'Erro ao criar SAF. Tente novamente.', 'request_id': request_id}), 500
