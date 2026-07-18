from flask import Blueprint, request, jsonify
import os
import logging
from datetime import datetime, timezone
from supabase import create_client, Client
import sap_client

logger = logging.getLogger(__name__)

sap_bp = Blueprint('sap', __name__)


def _get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL e SUPABASE_KEY não configuradas.")
    return create_client(url, key)


def _log(sb: Client, evento: str, payload: dict):
    try:
        sb.table('logs_auditoria').insert({"evento": evento, "payload": payload}).execute()
    except Exception:
        pass


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────
# CACHE: Locais de Instalação
# ──────────────────────────────────────────────────────────────

@sap_bp.route('/locais', methods=['GET'])
def listar_locais():
    """Retorna Locais de Instalação em cache (tabela locais_instalacao)."""
    try:
        sb = _get_supabase()
        res = sb.table('locais_instalacao') \
            .select('id, codigo, descricao') \
            .eq('ativo', True) \
            .order('descricao') \
            .execute()
        return jsonify(res.data), 200
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# ──────────────────────────────────────────────────────────────
# CACHE: Equipamentos (filtrado por local)
# ──────────────────────────────────────────────────────────────

@sap_bp.route('/equipamentos', methods=['GET'])
def listar_equipamentos():
    """
    Retorna Equipamentos em cache.
    Query param: local_id (int) — filtra por local de instalação.
    """
    local_id = request.args.get('local_id')
    try:
        sb = _get_supabase()
        q = sb.table('equipamentos') \
            .select('id, codigo, descricao, local_instalacao_id') \
            .eq('ativo', True)
        if local_id:
            q = q.eq('local_instalacao_id', local_id)
        res = q.order('descricao').execute()
        return jsonify(res.data), 200
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# ──────────────────────────────────────────────────────────────
# CACHE: Sintomas
# ──────────────────────────────────────────────────────────────

@sap_bp.route('/sintomas', methods=['GET'])
def listar_sintomas():
    """Retorna Sintomas em cache (tabela sintomas_catalogo)."""
    try:
        sb = _get_supabase()
        res = sb.table('sintomas_catalogo') \
            .select('id, codigo, descricao, categoria') \
            .eq('ativo', True) \
            .order('descricao') \
            .execute()
        return jsonify(res.data), 200
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# ──────────────────────────────────────────────────────────────
# CRIAR NOTA DE MANUTENÇÃO SAP
# POST /api/sap/criar-nota/<saf_id>
# Chamado automaticamente pelo CCM ao aprovar (routes/ccm.py).
# Também pode ser disparado manualmente para reprocessar falhas.
# ──────────────────────────────────────────────────────────────

@sap_bp.route('/criar-nota/<string:saf_id>', methods=['POST'])
def criar_nota(saf_id):
    """
    Cria Nota de Manutenção no SAP para a SAF informada.
    Retorna o QMNUM instantaneamente para o CCM.
    """
    try:
        sb = _get_supabase()

        # 1. Verifica aprovação
        ccm_res = sb.table('saf_solicitacoes') \
            .select('status, tipo_nota') \
            .eq('id', saf_id) \
            .execute()
        if not ccm_res.data or ccm_res.data[0]['status'] != 'APROVADA':
            return jsonify({"erro": "SAF não está com status APROVADA."}), 400

        tipo_nota = ccm_res.data[0].get('tipo_nota', 'YP')

        # 2. Verifica se nota já foi criada com sucesso
        integ_res = sb.table('saf_integracao_sap') \
            .select('qmnum, status_integracao, payload_resposta') \
            .eq('solicitacao_id', saf_id) \
            .execute()
        if integ_res.data:
            integ = integ_res.data[0]
            if integ.get('status_integracao') == 'SUCESSO' and integ.get('qmnum'):
                return jsonify({"mensagem": "Nota já criada.", "qmnum": integ['qmnum']}), 200

        # 3. Busca dados completos da SAF
        saf_res = sb.table('saf_solicitacoes').select('*').eq('id', saf_id).execute()
        if not saf_res.data:
            return jsonify({"erro": "SAF não encontrada."}), 404

        saf = saf_res.data[0]
        saf['tipo_nota'] = tipo_nota

        # Reaproveita metadados da validacao CCM (quando disponiveis)
        integ_payload = (integ_res.data[0].get('payload_resposta') if integ_res.data else None) or {}
        if isinstance(integ_payload, dict):
            if integ_payload.get('centro_trabalho'):
                saf['centro_trabalho'] = integ_payload.get('centro_trabalho')

        # Enriquece dados de sintoma para LongText e campos SAP (QMGRP/QMCOD)
        sintoma_id = saf.get('sintoma_id')
        if sintoma_id:
            sint = (
                sb.table('sintomas_catalogo')
                .select('descricao, grupo, codigo_item')
                .eq('id', sintoma_id)
                .limit(1)
                .execute()
            )
            if sint.data:
                sintoma = sint.data[0]
                saf['sintoma_descricao'] = sintoma.get('descricao')
                saf['qmgrp'] = sintoma.get('grupo')
                saf['qmcod'] = sintoma.get('codigo_item')

        # 4. Chama API do SAP
        resultado = sap_client.sap_criar_nota(saf)
        qmnum = resultado['qmnum']
        raw_result = resultado.get('raw', {}) if isinstance(resultado, dict) else {}
        aufnr = (
            (resultado.get('aufnr') if isinstance(resultado, dict) else None)
            or (raw_result.get('aufnr') if isinstance(raw_result, dict) else None)
            or (raw_result.get('numero_ordem_sap') if isinstance(raw_result, dict) else None)
        )

        payload_envio = {
            "ticket_saf":       saf.get('ticket_saf'),
            "tipo_nota":        tipo_nota,
            "local_instalacao": saf.get('local_instalacao'),
            "equipamento":      saf.get('equipamento'),
            "prioridade":       saf.get('prioridade'),
        }

        # 5. Persiste resultado
        sb.table('saf_integracao_sap').upsert({
            "solicitacao_id":     saf_id,
            "qmnum":              qmnum,
            "aufnr":              aufnr,
            "numero_ordem_sap":   aufnr,
            "tipo_nota":          tipo_nota,
            "status_integracao":  "SUCESSO",
            "payload_envio":      payload_envio,
            "payload_resposta":   raw_result,
            "ultima_tentativa_em": _agora(),
            "mensagem_erro":      None,
        }).execute()

        _log(sb, "INTEGRACAO_SAP_SUCESSO", {"saf_id": saf_id, "qmnum": qmnum})

        return jsonify({"mensagem": "Nota criada com sucesso.", "qmnum": qmnum, "aufnr": aufnr}), 200

    except Exception as e:
        # Registra falha sem quebrar o fluxo
        try:
            sb = _get_supabase()
            sb.table('saf_integracao_sap').upsert({
                "solicitacao_id":     saf_id,
                "status_integracao":  "ERRO",
                "mensagem_erro":      str(e),
                "ultima_tentativa_em": _agora(),
            }).execute()
            _log(sb, "INTEGRACAO_SAP_ERRO", {"saf_id": saf_id, "erro": str(e)})
        except Exception:
            pass
        logger.exception("Erro ao criar nota SAP para saf_id=%s", saf_id)
        return jsonify({"erro": str(e)}), 500


# ──────────────────────────────────────────────────────────────
# CANCELAR NOTA DE MANUTENÇÃO SAP (transação atômica)
# POST /api/sap/cancelar-nota/<saf_id>
# Body JSON: { "motivo": "..." }
# ──────────────────────────────────────────────────────────────

@sap_bp.route('/cancelar-nota/<string:saf_id>', methods=['POST'])
def cancelar_nota(saf_id):
    """
    Cancela a Nota no SAP e, somente após confirmação, marca como cancelada no SAF.
    Se o SAP falhar, o SAF NÃO é cancelado (transação atômica conforme spec).
    Bloqueado se houver Ordem de Manutenção vinculada (AUFNR).
    """
    dados = request.json or {}
    motivo = (dados.get('motivo') or '').strip()
    if not motivo:
        return jsonify({"erro": "Motivo de cancelamento é obrigatório."}), 400

    try:
        sb = _get_supabase()

        # 1. Busca registro de integração
        integ_res = sb.table('saf_integracao_sap') \
            .select('qmnum, aufnr, numero_ordem_sap, status_integracao') \
            .eq('solicitacao_id', saf_id) \
            .execute()

        if not integ_res.data or not integ_res.data[0].get('qmnum'):
            return jsonify({"erro": "Nenhuma Nota SAP encontrada para esta SAF."}), 404

        integ = integ_res.data[0]
        qmnum = integ['qmnum']

        # 2. Bloqueio se houver Ordem de Manutenção (AUFNR) — QMEL-AUFNR
        aufnr = integ.get('aufnr') or integ.get('numero_ordem_sap')
        if aufnr:
            return jsonify({
                "erro": f"Cancelamento bloqueado: Ordem de Manutenção {aufnr} já foi gerada para esta nota."
            }), 409

        # 3. Cancela no SAP primeiro (síncrono — se falhar, não cancela no SAF)
        sap_client.sap_cancelar_nota(qmnum)

        # 4. Só atualiza o SAF após confirmação do SAP
        sb.table('saf_integracao_sap').update({
            "status_integracao": "CANCELADO",
            "ultima_tentativa_em": _agora(),
        }).eq('solicitacao_id', saf_id).execute()

        sb.table('saf_solicitacoes') \
            .update({'status': 'CANCELADA', 'motivo_cancelamento': motivo}) \
            .eq('id', saf_id) \
            .execute()

        _log(sb, "CANCELAMENTO_NOTA_SAP_SUCESSO", {
            "saf_id": saf_id, "qmnum": qmnum, "motivo": motivo
        })

        return jsonify({"mensagem": "Nota cancelada com sucesso.", "qmnum": qmnum}), 200

    except Exception as e:
        try:
            _log(_get_supabase(), "CANCELAMENTO_NOTA_SAP_ERRO", {
                "saf_id": saf_id, "erro": str(e)
            })
        except Exception:
            pass
        logger.exception("Erro ao cancelar nota SAP para saf_id=%s", saf_id)
        return jsonify({"erro": f"Falha ao cancelar nota no SAP: {str(e)}"}), 500


# ──────────────────────────────────────────────────────────────
# SYNC DE STATUS DAS NOTAS (Job periódico)
# POST /api/sap/sync-status
# Consulta SAP para verificar AUFNR e JEST-STAT de notas abertas.
# ──────────────────────────────────────────────────────────────

@sap_bp.route('/sync-status', methods=['POST'])
def sync_status():
    """
    Consulta o status de cada Nota no SAP e atualiza saf_integracao_sap.
    - Preenche AUFNR quando Ordem for gerada (bloqueia cancelamento no SAF).
    - Reflete status CANCL/CLSD do SAP no SAF.
    Chamado a cada 2 minutos pelo job agendado ou manualmente.
    """
    try:
        sb = _get_supabase()

        # Busca notas com sucesso e sem ordem ainda vinculada
        registros = sb.table('saf_integracao_sap') \
            .select('solicitacao_id, qmnum') \
            .eq('status_integracao', 'SUCESSO') \
            .not_.is_('qmnum', 'null') \
            .execute()

        atualizados = 0
        erros = 0

        for reg in (registros.data or []):
            qmnum  = reg['qmnum']
            saf_id = reg['solicitacao_id']
            try:
                nota = sap_client.sap_consultar_nota(qmnum)

                # Campos SAP — diferentes serviços usam nomes distintos
                aufnr     = nota.get('MaintenanceOrder') or nota.get('aufnr') or nota.get('AUFNR')
                jest_stat = nota.get('SystemStatus')     or nota.get('jest_stat') or nota.get('JEST_STAT')

                update_data = {"ultima_tentativa_em": _agora()}
                if aufnr:
                    update_data["aufnr"] = aufnr
                    update_data["numero_ordem_sap"] = aufnr
                if jest_stat:
                    update_data["jest_stat"] = jest_stat
                    # Se cancelada no SAP, reflete no SAF
                    if 'CANCL' in str(jest_stat).upper():
                        update_data["status_integracao"] = "CANCELADO"

                sb.table('saf_integracao_sap') \
                    .update(update_data) \
                    .eq('solicitacao_id', saf_id) \
                    .execute()

                atualizados += 1

            except Exception as ex:
                logger.warning("Erro ao consultar nota %s: %s", qmnum, ex)
                erros += 1

        return jsonify({"atualizados": atualizados, "erros": erros}), 200

    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# ──────────────────────────────────────────────────────────────
# SYNC DE DADOS MESTRES (acionamento manual/admin)
# POST /api/sap/sync-mestres
# O job automático fica na Edge Function supabase/functions/sync-mestres-sap/
# ──────────────────────────────────────────────────────────────

@sap_bp.route('/sync-mestres', methods=['POST'])
def sync_mestres():
    """
    Sincroniza cache de Locais e Equipamentos a partir do SAP.
    Pode ser acionado manualmente pelo Admin para forçar atualização.
    """
    try:
        sb = _get_supabase()
        agora = _agora()
        locais_sync = 0
        equip_sync  = 0

        # — Locais de Instalação (TPLNR / IL03) —
        locais_sap = sap_client.sap_listar_locais()
        for item in locais_sap:
            codigo   = item.get('FunctionalLocation') or item.get('codigo')
            descricao = item.get('FunctionalLocationName') or item.get('descricao', '')
            if not codigo:
                continue
            sb.table('locais_instalacao').upsert(
                {"id_sap": codigo, "codigo": codigo, "descricao": descricao, "ativo": True, "sincronizado_em": agora},
                ignore_duplicates=False,
            ).execute()
            locais_sync += 1

        # — Equipamentos (EQUNR / IE03) —
        equip_sap = sap_client.sap_listar_equipamentos()
        for item in equip_sap:
            codigo    = item.get('Equipment') or item.get('codigo')
            descricao = item.get('EquipmentName') or item.get('descricao', '')
            tplnr     = item.get('FunctionalLocation') or item.get('local_instalacao')
            if not codigo:
                continue

            local_id = None
            if tplnr:
                local_res = sb.table('locais_instalacao') \
                    .select('id_sap') \
                    .eq('id_sap', tplnr) \
                    .execute()
                if local_res.data:
                    local_id = local_res.data[0]['id_sap']

            sb.table('equipamentos').upsert(
                {
                    "id_sap": codigo,
                    "codigo": codigo,
                    "descricao": descricao,
                    "local_id_sap": local_id,
                    "ativo": True,
                    "sincronizado_em": agora,
                },
                ignore_duplicates=False,
            ).execute()
            equip_sync += 1

        _log(sb, "SYNC_MESTRES_SAP", {
            "locais": locais_sync, "equipamentos": equip_sync, "em": agora
        })

        return jsonify({"locais": locais_sync, "equipamentos": equip_sync}), 200

    except Exception as e:
        logger.exception("Erro ao sincronizar dados mestres SAP")
        return jsonify({"erro": str(e)}), 500


# ──────────────────────────────────────────────────────────────
# STATUS DE INTEGRAÇÃO DE UMA SAF
# GET /api/sap/status/<saf_id>
# ──────────────────────────────────────────────────────────────

@sap_bp.route('/status/<string:saf_id>', methods=['GET'])
def status_integracao(saf_id):
    """Retorna o status da integração SAP para uma SAF."""
    try:
        sb = _get_supabase()
        res = sb.table('saf_integracao_sap') \
            .select('qmnum, aufnr, numero_ordem_sap, status_integracao, jest_stat, tipo_nota, ultima_tentativa_em, mensagem_erro') \
            .eq('solicitacao_id', saf_id) \
            .execute()
        if not res.data:
            return jsonify({"status_integracao": "PENDENTE"}), 200
        return jsonify(res.data[0]), 200
    except Exception as e:
        return jsonify({"erro": str(e)}), 500