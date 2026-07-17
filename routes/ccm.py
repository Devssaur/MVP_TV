from flask import Blueprint, request, jsonify
import os
import logging
from supabase import create_client, Client
from datetime import datetime, timezone
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

logger = logging.getLogger(__name__)

ccm_bp = Blueprint('ccm', __name__)


class AvaliarSafPayload(BaseModel):
    model_config = ConfigDict(extra='ignore')

    status: str
    motivo_devolucao: str | None = None
    avaliador_id: str | None = None
    prioridade: str | None = None
    tipo_nota: str | None = 'YP'
    qmnum: str | None = Field(default=None, max_length=30)
    motivo_cancelamento: str | None = None


def _erro_interno_padrao():
    return jsonify({'erro': 'Nao foi possivel processar sua solicitacao. Tente novamente.'}), 500

def _get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("Variaveis SUPABASE_URL e SUPABASE_SERVICE_KEY nao configuradas.")
    return create_client(url, key)


def _normalize_prioridade(valor) -> str:
    p = str(valor or '').strip().upper()
    mapping = {
        '1': 'BAIXA',
        '2': 'MEDIA',
        '3': 'ALTA',
        '4': 'CRITICA',
        'BAIXA': 'BAIXA',
        'MEDIA': 'MEDIA',
        'MÉDIA': 'MEDIA',
        'ALTA': 'ALTA',
        'CRITICA': 'CRITICA',
        'CRÍTICA': 'CRITICA',
    }
    return mapping.get(p, '')


# ==========================================
# 1. ROTA GET: Listar SAFs para a fila CCM (exceto devolvidas)
# ==========================================
@ccm_bp.route('/pendentes', methods=['GET'])
@require_auth(('CCM', 'Administrador'))
def listar_pendentes():
    try:
        supabase = _get_supabase_client()
        resposta = supabase.table('saf_solicitacoes') \
            .select(
                'id, ticket_saf, titulo_falha, descricao_longa, '
                'local_instalacao, local_instalacao_id, equipamento, equipamento_id, '
                'sintoma_id, prioridade, data_inicio_avaria, hora_inicio_avaria, '
                'notificador_id, notificador_nome, notificador_area, '
                'anexo_evidencia_url, criado_em, '
                'status, motivo_devolucao, motivo_cancelamento, '
                'atualizado_sap, tipo_nota, qmnum_duplicata, data_avaliacao, '
                'saf_integracao_sap(qmnum, tipo_nota, status_integracao, mensagem_erro)'
            ) \
            .neq('status', 'DEVOLVIDA') \
            .order('criado_em', desc=False) \
            .execute()
        return jsonify(resposta.data), 200
    except Exception:
        logger.exception('Erro interno ao listar pendentes CCM')
        return _erro_interno_padrao()


# ==========================================
# 2. ROTA PUT: Avaliar a SAF (Aceitar = APROVADA / Recusar = DEVOLVIDA)
# ==========================================
@ccm_bp.route('/avaliar/<string:solicitacao_id>', methods=['PUT'])
@require_auth(('CCM', 'Administrador'))
def avaliar_saf(solicitacao_id):
    dados = request.get_json(silent=True) or {}
    try:
        payload = AvaliarSafPayload.model_validate(dados)
    except ValidationError as exc:
        return jsonify({'erro': 'Dados invalidos para avaliacao.', 'detalhes': exc.errors()}), 400

    if not solicitacao_id.strip():
        return jsonify({'erro': 'ID da solicitacao invalido.'}), 400

    novo_status = payload.status
    motivo       = (payload.motivo_devolucao or '').strip()
    avaliador_id = get_current_user_context().get('id')
    prioridade = _normalize_prioridade(payload.prioridade)
    qmnum_manual = (payload.qmnum or '').strip() or None

    if novo_status not in ('APROVADA', 'DEVOLVIDA', 'CANCELADA'):
        return jsonify({'erro': 'Status invalido. Use APROVADA, DEVOLVIDA ou CANCELADA.'}), 400
    if novo_status == 'DEVOLVIDA' and not motivo:
        return jsonify({"erro": "Informe o motivo da devolução."}), 400
    if novo_status == 'CANCELADA':
        motivo_cancelamento = (payload.motivo_cancelamento or '').strip()
        if not motivo_cancelamento:
            return jsonify({'erro': 'Informe o motivo do cancelamento.'}), 400

    try:
        supabase = _get_supabase_client()
        update_data = {
            "status": novo_status,
            "avaliado_por": avaliador_id,
            "data_avaliacao": datetime.now(timezone.utc).isoformat()
        }
        if prioridade:
            update_data["prioridade"] = prioridade
        if novo_status == 'DEVOLVIDA':
            update_data["motivo_devolucao"] = motivo
        if novo_status == 'CANCELADA':
            update_data['motivo_cancelamento'] = (payload.motivo_cancelamento or '').strip()

        supabase.table('saf_solicitacoes') \
            .update(update_data) \
            .eq('id', solicitacao_id) \
            .execute()

        qmnum    = None
        erro_sap = None

        if novo_status == 'APROVADA':
            tipo_nota = payload.tipo_nota or 'YP'

            # Salva tipo_nota escolhido pelo CCM
            supabase.table('saf_solicitacoes') \
                .update({'tipo_nota': tipo_nota}) \
                .eq('id', solicitacao_id) \
                .execute()

            try:
                saf = (
                    supabase.table('saf_solicitacoes')
                    .select('equipamento_id, local_instalacao_id, sintoma_id, ticket_saf, prioridade')
                    .eq('id', solicitacao_id)
                    .maybe_single()
                    .execute()
                )
                saf = saf.data or {}

                status_integracao = 'PENDENTE'
                mensagem = 'Aguardando registro manual do numero SAP.'
                if qmnum_manual:
                    status_integracao = 'SUCESSO'
                    mensagem = None
                    qmnum = qmnum_manual

                supabase.table('saf_integracao_sap').upsert({
                    'solicitacao_id':      solicitacao_id,
                    'qmnum':               qmnum_manual,
                    'tipo_nota':           tipo_nota,
                    'status_integracao':   status_integracao,
                    'ultima_tentativa_em': datetime.now(timezone.utc).isoformat(),
                    'mensagem_erro':       mensagem,
                    'payload_resposta':    {
                        'modo': 'manual',
                        'observacao': 'Integracao automatica SAP desativada no backend.'
                    },
                }).execute()

                supabase.table('logs_auditoria').insert({
                    'evento': 'REGISTRO_QMNUM_MANUAL' if qmnum_manual else 'APROVACAO_SEM_QMNUM',
                    'payload': {'saf_id': solicitacao_id, 'qmnum': qmnum_manual},
                }).execute()

            except Exception as sap_err:
                erro_sap = 'Nao foi possivel registrar os dados de integracao.'
                logger.error('Falha ao registrar integracao manual (saf_id=%s): %s', solicitacao_id, sap_err)

            resposta = {'mensagem': 'SAF aprovada.', 'qmnum': qmnum}
            if erro_sap:
                resposta['aviso_sap'] = (
                    'Aprovacao registrada, mas houve falha ao registrar os dados SAP manualmente.'
                )

            # ── Marca duplicatas ──────────────────────────────────────────
            # Regra: mesma SAF = mesmo local + mesmo equipamento + mesmo sintoma.
            # Só marca se AMBAS tiverem sintoma_id definido e forem iguais.
            # Isso evita falsos positivos quando a SAF aprovada não tem sintoma.
            duplicatas_ids = []
            if qmnum:
                try:
                    equip_id   = saf.get('equipamento_id')
                    local_id   = saf.get('local_instalacao_id')
                    sintoma_id = saf.get('sintoma_id')

                    if equip_id and sintoma_id:
                        abertas = supabase.table('saf_solicitacoes') \
                            .select('id, local_instalacao_id, equipamento_id, sintoma_id') \
                            .eq('status', 'ABERTA') \
                            .neq('id', solicitacao_id) \
                            .execute()

                        for r in (abertas.data or []):
                            if r.get('equipamento_id') != equip_id:
                                continue
                            if r.get('local_instalacao_id') != local_id:
                                continue
                            # Exige sintoma igual em ambas — evita marcar SAFs de avaria diferente
                            if r.get('sintoma_id') != sintoma_id:
                                continue
                            duplicatas_ids.append(r['id'])

                        agora = datetime.now(timezone.utc).isoformat()
                        for dup_id in duplicatas_ids:
                            supabase.table('saf_solicitacoes').update({
                                "status":          "DUPLICADA",
                                "qmnum_duplicata": qmnum,
                                "tipo_nota":       tipo_nota,
                                "data_avaliacao":  agora,
                                "avaliado_por":    avaliador_id,
                            }).eq('id', dup_id).execute()

                        if duplicatas_ids:
                            logger.info(
                                "Marcadas %d SAFs como DUPLICADA (local=%s equip=%s sintoma=%s) "
                                "→ QMNUM %s: %s",
                                len(duplicatas_ids), local_id, equip_id,
                                sintoma_id, qmnum, duplicatas_ids,
                            )
                except Exception:
                    logger.exception('Erro ao marcar duplicatas (nao bloqueante)')

            resposta["duplicatas"] = len(duplicatas_ids)
            return jsonify(resposta), 200

        return jsonify({"mensagem": f"SAF atualizada para {novo_status}."}), 200
    except AuthApiError:
        logger.exception('Erro de autenticacao Supabase ao avaliar SAF id=%s', solicitacao_id)
        return jsonify({'erro': 'Falha de autenticacao ao processar a solicitacao.'}), 401
    except PostgrestAPIError:
        logger.exception('Erro de dados Supabase ao avaliar SAF id=%s', solicitacao_id)
        return jsonify({'erro': 'Nao foi possivel salvar os dados da avaliacao.'}), 500
    except Exception:
        logger.exception('Erro interno ao avaliar SAF id=%s', solicitacao_id)
        return _erro_interno_padrao()


# ==========================================
# 2.1 ROTA PUT: Atualizar criticidade/prioridade pelo CCM
# ==========================================
@ccm_bp.route('/prioridade/<string:solicitacao_id>', methods=['PUT'])
@require_auth(('CCM', 'Administrador'))
def atualizar_prioridade_ccm(solicitacao_id):
    dados = request.json or {}
    prioridade = _normalize_prioridade(dados.get('prioridade'))
    if not prioridade:
        return jsonify({"erro": "Prioridade inválida. Use BAIXA, MEDIA, ALTA ou CRITICA."}), 400

    try:
        supabase = _get_supabase_client()
        supabase.table('saf_solicitacoes') \
            .update({'prioridade': prioridade}) \
            .eq('id', solicitacao_id) \
            .execute()
        return jsonify({'mensagem': 'Prioridade atualizada.', 'prioridade': prioridade}), 200
    except AuthApiError:
        logger.exception('Erro de autenticacao Supabase ao atualizar prioridade id=%s', solicitacao_id)
        return jsonify({'erro': 'Falha de autenticacao ao processar a solicitacao.'}), 401
    except PostgrestAPIError:
        logger.exception('Erro de dados Supabase ao atualizar prioridade id=%s', solicitacao_id)
        return jsonify({'erro': 'Nao foi possivel salvar os dados da prioridade.'}), 500
    except Exception:
        logger.exception('Erro interno ao atualizar prioridade id=%s', solicitacao_id)
        return _erro_interno_padrao()


# ==========================================
# 2.2 ROTA PUT: Marcar ordens como DUPLICADA em lote
# ==========================================
@ccm_bp.route('/duplicar-lote', methods=['PUT'])
@require_auth(('CCM', 'Administrador'))
def duplicar_lote_ccm():
    dados = request.json or {}
    ids = dados.get('ids') or []
    avaliador_id = get_current_user_context().get('id')

    if not isinstance(ids, list) or not ids:
        return jsonify({'erro': 'Informe uma lista de IDs para duplicar.'}), 400

    # Remove vazios e duplicados preservando ordem
    ids_limpos = []
    vistos = set()
    for sid in ids:
        sid_txt = str(sid or '').strip()
        if not sid_txt or sid_txt in vistos:
            continue
        vistos.add(sid_txt)
        ids_limpos.append(sid_txt)

    if not ids_limpos:
        return jsonify({'erro': 'Nenhum ID valido foi informado.'}), 400

    try:
        supabase = _get_supabase_client()
        abertas = supabase.table('saf_solicitacoes') \
            .select('id') \
            .in_('id', ids_limpos) \
            .eq('status', 'ABERTA') \
            .execute()

        abertas_ids = [r.get('id') for r in (abertas.data or []) if r.get('id')]
        if not abertas_ids:
            return jsonify({'erro': 'Nenhuma ordem aberta encontrada para marcar como duplicada.'}), 400

        agora = datetime.now(timezone.utc).isoformat()
        for sid in abertas_ids:
            supabase.table('saf_solicitacoes') \
                .update({
                    'status': 'DUPLICADA',
                    'data_avaliacao': agora,
                    'avaliado_por': avaliador_id,
                }) \
                .eq('id', sid) \
                .execute()

        try:
            supabase.table('logs_auditoria').insert({
                'evento': 'CCM_DUPLICADA_LOTE',
                'payload': {
                    'total_solicitado': len(ids_limpos),
                    'total_marcado': len(abertas_ids),
                    'ids': abertas_ids,
                    'avaliador_id': avaliador_id,
                },
            }).execute()
        except Exception:
            logger.exception('Falha ao registrar auditoria do lote de duplicadas')

        return jsonify({
            'mensagem': 'Ordens marcadas como duplicadas.',
            'total_marcado': len(abertas_ids),
            'ids_marcados': abertas_ids,
        }), 200
    except AuthApiError:
        logger.exception('Erro de autenticacao Supabase no lote de duplicadas')
        return jsonify({'erro': 'Falha de autenticacao ao processar a solicitacao.'}), 401
    except PostgrestAPIError:
        logger.exception('Erro de dados Supabase no lote de duplicadas')
        return jsonify({'erro': 'Nao foi possivel salvar os dados do lote.'}), 500
    except Exception:
        logger.exception('Erro interno no lote de duplicadas')
        return _erro_interno_padrao()


# ==========================================
# 3. ROTA PATCH: Alternar flag atualizado_sap
# ==========================================
@ccm_bp.route('/toggle-sap/<string:solicitacao_id>', methods=['PATCH'])
@require_auth(('CCM', 'Administrador'))
def toggle_sap(solicitacao_id):
    dados = request.json or {}
    novo_valor = bool(dados.get('atualizado_sap', False))
    try:
        supabase = _get_supabase_client()
        supabase.table('saf_solicitacoes') \
            .update({'atualizado_sap': novo_valor}) \
            .eq('id', solicitacao_id) \
            .execute()
        return jsonify({'atualizado_sap': novo_valor}), 200
    except AuthApiError:
        logger.exception('Erro de autenticacao Supabase ao alternar flag SAP id=%s', solicitacao_id)
        return jsonify({'erro': 'Falha de autenticacao ao processar a solicitacao.'}), 401
    except PostgrestAPIError:
        logger.exception('Erro de dados Supabase ao alternar flag SAP id=%s', solicitacao_id)
        return jsonify({'erro': 'Nao foi possivel salvar os dados da atualizacao.'}), 500
    except Exception:
        logger.exception('Erro interno ao alternar flag SAP id=%s', solicitacao_id)
        return _erro_interno_padrao()