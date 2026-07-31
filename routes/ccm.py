from flask import Blueprint, request, jsonify
import os
import logging
import re
import unicodedata
from supabase import create_client, Client
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from routes.security import require_auth, get_current_user_context

try:
    from gotrue.errors import AuthApiError
except Exception:  # pragma: no cover
    class AuthApiError(Exception):
        pass

try:
    from postgrest.exceptions import APIError as PostgrestAPIError
except Exception:  # pragma: no cover
    class PostgrestAPIError(Exception):
        pass

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
    aufnr: str | None = Field(default=None, max_length=30)
    texto_breve_nota: str | None = Field(default=None, max_length=500)
    texto_longo_nota: str | None = None
    motivo_cancelamento: str | None = None
    centro_trabalho: str | None = Field(default=None, max_length=120)
    sintoma_id: str | None = Field(default=None, max_length=100)
    sintoma_descricao: str | None = Field(default=None, max_length=500)
    ids_duplicatas: list[str] | None = None


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
        '1': 'CRITICA',
        '2': 'ALTA',
        '3': 'MEDIA',
        '4': 'BAIXA',
        'MUITO ELEVADO': 'CRITICA',
        'MUITO ELEVADA': 'CRITICA',
        'ELEVADO': 'ALTA',
        'ELEVADA': 'ALTA',
        'MEDIO': 'MEDIA',
        'MÉDIO': 'MEDIA',
        'BAIXA': 'BAIXA',
        'MEDIA': 'MEDIA',
        'MÉDIA': 'MEDIA',
        'ALTA': 'ALTA',
        'CRITICA': 'CRITICA',
        'CRÍTICA': 'CRITICA',
    }
    return mapping.get(p, '')


def _resolver_sintoma_catalogo_id(supabase: Client, sintoma_id_raw: str | None) -> str | None:
    """Resolve o sintoma para um id valido em sintomas_catalogo.

    Aceita id ja existente em sintomas_catalogo.
    Se vier id da tabela sintomas (uuid legado), tenta mapear por descricao.
    """
    sintoma_id = str(sintoma_id_raw or '').strip()
    if not sintoma_id:
        return None

    # 1) Caminho preferencial: id ja existe no catalogo atual.
    cat = (
        supabase.table('sintomas_catalogo')
        .select('id')
        .eq('id', sintoma_id)
        .limit(1)
        .execute()
    )
    if cat.data:
        return sintoma_id

    # 2) Compatibilidade: id veio da tabela legada sintomas (uuid).
    sint_legado = (
        supabase.table('sintomas')
        .select('id, descricao')
        .eq('id', sintoma_id)
        .limit(1)
        .execute()
    )
    legado = (sint_legado.data or [{}])[0]
    descricao = str(legado.get('descricao') or '').strip()
    if not descricao:
        return None

    # 3) Match por descricao no catalogo.
    by_desc = _resolver_sintoma_catalogo_por_descricao(supabase, descricao)
    if by_desc:
        return by_desc

    # 4) Fallback por codigo/codigo_item para compatibilidade de payloads legados.
    cat_by_code = (
        supabase.table('sintomas_catalogo')
        .select('id, codigo, codigo_item')
        .eq('ativo', True)
        .or_(f'codigo.eq.{sintoma_id},codigo_item.eq.{sintoma_id}')
        .limit(1)
        .execute()
    )
    if cat_by_code.data:
        return str((cat_by_code.data[0] or {}).get('id') or '').strip() or None

    return None

def _resolver_sintoma_catalogo_por_descricao(supabase: Client, descricao_raw: str | None) -> str | None:
    descricao = str(descricao_raw or '').strip()
    if not descricao:
        return None

    # Preferencia por match exato.
    exato = (
        supabase.table('sintomas_catalogo')
        .select('id, descricao')
        .eq('ativo', True)
        .eq('descricao', descricao)
        .limit(1)
        .execute()
    )
    if exato.data:
        return str((exato.data[0] or {}).get('id') or '').strip() or None

    # Fallback ilike.
    ilike = (
        supabase.table('sintomas_catalogo')
        .select('id, descricao')
        .eq('ativo', True)
        .ilike('descricao', descricao)
        .limit(1)
        .execute()
    )
    if ilike.data:
        return str((ilike.data[0] or {}).get('id') or '').strip() or None

    # Fallback normalizado (acento/pontuacao/espacos).
    def _norm_text(value: str) -> str:
        base = unicodedata.normalize('NFD', str(value or ''))
        sem_acentos = ''.join(ch for ch in base if unicodedata.category(ch) != 'Mn')
        lowered = sem_acentos.lower()
        cleaned = re.sub(r'[^a-z0-9]+', ' ', lowered)
        return re.sub(r'\s+', ' ', cleaned).strip()

    alvo = _norm_text(descricao)
    if not alvo:
        return None

    cat_all = (
        supabase.table('sintomas_catalogo')
        .select('id, descricao')
        .eq('ativo', True)
        .limit(2000)
        .execute()
    )
    for row in (cat_all.data or []):
        if _norm_text(row.get('descricao') or '') == alvo:
            return str(row.get('id') or '').strip() or None

    return None


def _extrair_sintoma_do_texto_longo(texto_longo_raw: str | None) -> str:
    texto = str(texto_longo_raw or '')
    if not texto:
        return ''
    m = re.search(r'SINTOMA:\s*([^,\n\r]+)', texto, flags=re.IGNORECASE)
    return str(m.group(1)).strip() if m and m.group(1) else ''


def _espelhar_sintoma_legacy_no_catalogo(supabase: Client, sintoma_id: str) -> bool:
    """Cria/atualiza um espelho tecnico em sintomas_catalogo para satisfazer a FK atual.

    Regra de negocio segue usando id da tabela sintomas; este espelho evita falha de integridade
    enquanto o schema ainda referencia sintomas_catalogo.
    """
    sid = str(sintoma_id or '').strip()
    if not sid:
        return False

    sint = (
        supabase.table('sintomas')
        .select('id, descricao, grupo_id, ativo')
        .eq('id', sid)
        .limit(1)
        .execute()
    )
    row = (sint.data or [{}])[0]
    if not row or not row.get('id'):
        return False

    grupo_codigo = None
    grupo_id = row.get('grupo_id')
    if grupo_id:
        grp = (
            supabase.table('grupos')
            .select('codigo')
            .eq('id', grupo_id)
            .limit(1)
            .execute()
        )
        grp_row = (grp.data or [{}])[0]
        grupo_codigo = str(grp_row.get('codigo') or '').strip() or None

    payload = {
        'id': sid,
        'descricao': str(row.get('descricao') or '').strip(),
        'ativo': bool(row.get('ativo', True)),
    }
    if grupo_codigo:
        payload['grupo'] = grupo_codigo

    supabase.table('sintomas_catalogo').upsert(payload).execute()
    return True


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
                'id, ticket_saf, titulo_falha, descricao_longa, numero_ocorrencia, '
                'local_instalacao, local_instalacao_id, equipamento, equipamento_id, '
                'sistema_id, subsistema_id, '
                'via_numero, km_inicial, km_final, '
                'sintoma_id, prioridade, data_inicio_avaria, hora_inicio_avaria, '
                'notificador_id, notificador_nome, notificador_area, '
                'anexo_evidencia_url, criado_em, '
                'status, motivo_devolucao, motivo_cancelamento, '
                'atualizado_sap, tipo_nota, qmnum_duplicata, data_avaliacao, avaliado_por, '
                'ccm_texto_breve_nota, ccm_texto_longo_nota, ccm_numero_nota, ccm_numero_ordem, ccm_centro_trabalho, '
                'saf_integracao_sap(qmnum, aufnr, numero_ordem_sap, tipo_nota, status_integracao, mensagem_erro, texto_breve_nota, texto_longo_nota)'
            ) \
            .neq('status', 'DEVOLVIDA') \
            .order('criado_em', desc=False) \
            .execute()

        ids_avaliadores = []
        for item in (resposta.data or []):
            aval_id = str(item.get('avaliado_por') or '').strip()
            if aval_id and aval_id not in ids_avaliadores:
                ids_avaliadores.append(aval_id)

        nomes_por_id = {}
        if ids_avaliadores:
            usuarios_resp = supabase.table('usuarios') \
                .select('id, nome') \
                .in_('id', ids_avaliadores) \
                .execute()
            for usuario in (usuarios_resp.data or []):
                uid = str(usuario.get('id') or '').strip()
                if uid:
                    nomes_por_id[uid] = str(usuario.get('nome') or '').strip()

        for item in (resposta.data or []):
            aval_id = str(item.get('avaliado_por') or '').strip()
            item['aprovador_nome'] = nomes_por_id.get(aval_id, '')

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
    request_id = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
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
    aufnr_manual = (payload.aufnr or '').strip() or None
    ids_duplicatas = [str(item).strip() for item in (payload.ids_duplicatas or []) if str(item).strip()]
    texto_breve_nota = (payload.texto_breve_nota or '').strip()
    texto_longo_nota = (payload.texto_longo_nota or '').strip()
    centro_trabalho = (payload.centro_trabalho or '').strip()
    tipo_nota = (payload.tipo_nota or 'YP').strip().upper()

    if novo_status not in ('APROVADA', 'DEVOLVIDA', 'CANCELADA'):
        return jsonify({'erro': 'Status invalido. Use APROVADA, DEVOLVIDA ou CANCELADA.'}), 400
    if novo_status == 'APROVADA' and tipo_nota not in ('YP', 'YE'):
        return jsonify({'erro': 'Tipo de nota invalido. Use YP ou YE.'}), 400
    if novo_status == 'DEVOLVIDA' and not motivo:
        return jsonify({"erro": "Informe o motivo da devolução."}), 400
    if novo_status == 'CANCELADA':
        motivo_cancelamento = (payload.motivo_cancelamento or '').strip()
        if not motivo_cancelamento:
            return jsonify({'erro': 'Informe o motivo do cancelamento.'}), 400
    if novo_status == 'APROVADA' and not centro_trabalho:
        return jsonify({'erro': 'Informe o Centro de Trabalho para aprovar a SAF.'}), 400
    if novo_status == 'APROVADA' and not (payload.sintoma_id or '').strip():
        return jsonify({'erro': 'Informe o sintoma da falha para aprovar a SAF.'}), 400
    if novo_status == 'APROVADA' and not texto_breve_nota:
        return jsonify({'erro': 'Informe o texto breve da nota para aprovar a SAF.'}), 400
    if novo_status == 'APROVADA' and not texto_longo_nota:
        return jsonify({'erro': 'Informe o texto longo da nota para aprovar a SAF.'}), 400

    try:
        supabase = _get_supabase_client()
        sintoma_id_para_gravar = None
        sintoma_id_original = (payload.sintoma_id or '').strip() or None
        if payload.sintoma_id is not None:
            sintoma_id_para_gravar = sintoma_id_original
            if sintoma_id_original:
                sintoma_legado = (
                    supabase.table('sintomas')
                    .select('id')
                    .eq('id', sintoma_id_original)
                    .limit(1)
                    .execute()
                )
                if not sintoma_legado.data:
                    sintoma_id_para_gravar = None
            if sintoma_id_original and not sintoma_id_para_gravar:
                logger.warning(
                    '[AVALIAR_SAF][%s] sintoma_id sem correspondencia em sintomas. solicitacao_id=%s sintoma_id=%s',
                    request_id,
                    solicitacao_id,
                    sintoma_id_original,
                )
        if novo_status == 'APROVADA' and sintoma_id_original and not sintoma_id_para_gravar:
            return jsonify({'erro': 'Sintoma invalido para gravacao. Selecione um sintoma valido da lista.'}), 400

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
        if payload.sintoma_id is not None:
            update_data['sintoma_id'] = sintoma_id_para_gravar

        centro_info = None
        if novo_status == 'APROVADA':
            centro_q = (
                supabase.table('centros_trabalho')
                .select('codigo, denominacao, ativo')
                .eq('codigo', centro_trabalho)
                .limit(1)
                .execute()
            )
            if not centro_q.data:
                return jsonify({'erro': 'Centro de Trabalho invalido.'}), 400
            centro_info = centro_q.data[0]
            if centro_info.get('ativo') is False:
                return jsonify({'erro': 'Centro de Trabalho inativo.'}), 400

        try:
            supabase.table('saf_solicitacoes') \
                .update(update_data) \
                .eq('id', solicitacao_id) \
                .execute()
        except Exception as update_err:
            err_txt = str(update_err)
            fk_sintoma = (
                'saf_solicitacoes_sintoma_id_fkey' in err_txt
                or (
                    'violates foreign key constraint' in err_txt
                    and 'sintoma_id' in err_txt
                    and 'sintomas_catalogo' in err_txt
                )
            )
            if fk_sintoma and sintoma_id_para_gravar:
                logger.warning(
                    '[AVALIAR_SAF][%s] FK de sintoma detectada; sincronizando espelho tecnico no catalogo. solicitacao_id=%s sintoma_id=%s',
                    request_id,
                    solicitacao_id,
                    sintoma_id_para_gravar,
                )
                espelhou = _espelhar_sintoma_legacy_no_catalogo(supabase, sintoma_id_para_gravar)
                if espelhou:
                    supabase.table('saf_solicitacoes') \
                        .update(update_data) \
                        .eq('id', solicitacao_id) \
                        .execute()
                else:
                    raise
            else:
                raise

        qmnum    = None
        erro_sap = None

        if novo_status == 'APROVADA':
            # Salva tipo_nota escolhido pelo CCM
            supabase.table('saf_solicitacoes') \
                .update({
                    'tipo_nota': tipo_nota,
                    'ccm_texto_breve_nota': texto_breve_nota,
                    'ccm_texto_longo_nota': texto_longo_nota,
                    'ccm_numero_nota': qmnum_manual,
                    'ccm_numero_ordem': aufnr_manual,
                    'ccm_centro_trabalho': centro_trabalho,
                }) \
                .eq('id', solicitacao_id) \
                .execute()

            try:
                saf = (
                    supabase.table('saf_solicitacoes')
                    .select('sistema_id, subsistema_id, equipamento, equipamento_id, ticket_saf, prioridade')
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
                    'aufnr':               aufnr_manual,
                    'numero_ordem_sap':    aufnr_manual,
                    'tipo_nota':           tipo_nota,
                    'status_integracao':   status_integracao,
                    'ultima_tentativa_em': datetime.now(timezone.utc).isoformat(),
                    'mensagem_erro':       mensagem,
                    'texto_breve_nota':    texto_breve_nota,
                    'texto_longo_nota':    texto_longo_nota,
                    'payload_resposta':    {
                        'modo': 'manual',
                        'observacao': 'Integracao automatica SAP desativada no backend.',
                        'centro_trabalho': centro_trabalho,
                        'centro_trabalho_denominacao': (centro_info or {}).get('denominacao'),
                        'sintoma_id': sintoma_id_para_gravar,
                        'sintoma_id_original': sintoma_id_original,
                        'texto_breve_nota': texto_breve_nota,
                        'texto_longo_nota': texto_longo_nota,
                        'aufnr': aufnr_manual,
                    },
                }).execute()

                supabase.table('logs_auditoria').insert({
                    'evento': 'REGISTRO_QMNUM_MANUAL' if qmnum_manual else 'APROVACAO_SEM_QMNUM',
                    'payload': {'saf_id': solicitacao_id, 'qmnum': qmnum_manual},
                }).execute()

            except Exception as sap_err:
                erro_sap = 'Nao foi possivel registrar os dados de integracao.'
                logger.error('Falha ao registrar integracao manual (saf_id=%s): %s', solicitacao_id, sap_err)

            resposta = {
                'mensagem': 'SAF aprovada.',
                'qmnum': qmnum,
                'aufnr': aufnr_manual,
                'tipo_nota': tipo_nota,
                'centro_trabalho': centro_trabalho,
                'centro_trabalho_denominacao': (centro_info or {}).get('denominacao'),
                'sintoma_id': sintoma_id_para_gravar,
                'sintoma_id_original': sintoma_id_original,
            }
            if erro_sap:
                resposta['aviso_sap'] = (
                    'Aprovacao registrada, mas houve falha ao registrar os dados SAP manualmente.'
                )

            # ── Marca duplicatas ──────────────────────────────────────────
            duplicatas_ids = []
            if qmnum_manual or aufnr_manual:
                try:
                    if ids_duplicatas:
                        duplicatas_ids = [sid for sid in ids_duplicatas if sid != solicitacao_id]
                    else:
                        # Regra: mesma SAF = mesmo sistema_id + mesmo subsistema_id + mesmo equipamento.
                        sistema_id = saf.get('sistema_id')
                        subsistema_id = saf.get('subsistema_id')
                        equipamento = str(saf.get('equipamento') or '').strip().upper()
                        equipamento_id = str(saf.get('equipamento_id') or '').strip()

                        if sistema_id and subsistema_id and (equipamento or equipamento_id):
                            abertas = supabase.table('saf_solicitacoes') \
                                .select('id, sistema_id, subsistema_id, equipamento, equipamento_id') \
                                .eq('status', 'ABERTA') \
                                .neq('id', solicitacao_id) \
                                .execute()

                            for r in (abertas.data or []):
                                if r.get('sistema_id') != sistema_id:
                                    continue
                                if r.get('subsistema_id') != subsistema_id:
                                    continue

                                r_equip = str(r.get('equipamento') or '').strip().upper()
                                r_equip_id = str(r.get('equipamento_id') or '').strip()

                                same_equip = bool(equipamento and r_equip and r_equip == equipamento)
                                same_equip_id = bool(equipamento_id and r_equip_id and r_equip_id == equipamento_id)
                                if not (same_equip or same_equip_id):
                                    continue
                                duplicatas_ids.append(r['id'])

                    agora = datetime.now(timezone.utc).isoformat()
                    for dup_id in duplicatas_ids:
                        supabase.table('saf_solicitacoes').update({
                            "status":          "APROVADA",
                            "qmnum_duplicata": qmnum_manual,
                            "tipo_nota":       tipo_nota,
                            "ccm_texto_breve_nota": texto_breve_nota,
                            "ccm_texto_longo_nota": texto_longo_nota,
                            "ccm_numero_nota": qmnum_manual,
                            "ccm_numero_ordem": aufnr_manual,
                            "ccm_centro_trabalho": centro_trabalho,
                            "data_avaliacao":  agora,
                            "avaliado_por":    avaliador_id,
                        }).eq('id', dup_id).execute()

                        supabase.table('saf_integracao_sap').upsert({
                            'solicitacao_id': dup_id,
                            'tipo_nota': tipo_nota,
                            'aufnr': aufnr_manual,
                            'numero_ordem_sap': aufnr_manual,
                            'status_integracao': 'PENDENTE',
                            'texto_breve_nota': texto_breve_nota,
                            'texto_longo_nota': texto_longo_nota,
                            'ultima_tentativa_em': agora,
                            'mensagem_erro': 'SAF marcada como duplicada da nota principal.',
                            'payload_resposta': {
                                'modo': 'duplicada',
                                'saf_referencia': solicitacao_id,
                                'qmnum_referencia': qmnum_manual,
                                'aufnr': aufnr_manual,
                                'centro_trabalho': centro_trabalho,
                            },
                        }).execute()

                    if duplicatas_ids:
                        logger.info(
                            "Marcadas %d SAFs como APROVADA (modo duplicata) → QMNUM %s: %s",
                            len(duplicatas_ids), qmnum_manual, duplicatas_ids,
                        )
                except Exception:
                    logger.exception('Erro ao marcar duplicatas (nao bloqueante)')

            resposta["duplicatas"] = len(duplicatas_ids)
            return jsonify(resposta), 200

        return jsonify({"mensagem": f"SAF atualizada para {novo_status}."}), 200
    except AuthApiError:
        logger.exception('[AVALIAR_SAF][%s] Erro de autenticacao Supabase ao avaliar SAF id=%s', request_id, solicitacao_id)
        return jsonify({'erro': 'Falha de autenticacao ao processar a solicitacao.', 'request_id': request_id}), 401
    except PostgrestAPIError:
        logger.exception('[AVALIAR_SAF][%s] Erro de dados Supabase ao avaliar SAF id=%s', request_id, solicitacao_id)
        return jsonify({'erro': 'Nao foi possivel salvar os dados da avaliacao.', 'request_id': request_id}), 500
    except Exception:
        logger.exception('[AVALIAR_SAF][%s] Erro interno ao avaliar SAF id=%s', request_id, solicitacao_id)
        return jsonify({'erro': 'Nao foi possivel processar sua solicitacao. Tente novamente.', 'request_id': request_id}), 500


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