import logging
import os
from collections import Counter, defaultdict
from datetime import date, datetime

from flask import Blueprint, jsonify, request
from supabase import Client, create_client

from routes.security import require_auth

manutencao_bp = Blueprint("manutencao_bp", __name__)
logger = logging.getLogger(__name__)

_supabase_client: Client | None = None


def _get_supabase_client() -> Client:
    global _supabase_client
    if _supabase_client is None:
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
        if not supabase_url or not supabase_key:
            raise RuntimeError("Variaveis SUPABASE_URL e SUPABASE_SERVICE_KEY não configuradas.")
        _supabase_client = create_client(supabase_url, supabase_key)
    return _supabase_client


def _parse_date(raw: str | None) -> date | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _to_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _in_date_range(iso_value: str | None, start_date: date | None, end_date: date | None) -> bool:
    item_date = _to_date(iso_value)
    if item_date is None:
        return False
    if start_date and item_date < start_date:
        return False
    if end_date and item_date > end_date:
        return False
    return True


def _top_rows(counter: Counter, limit: int = 10) -> list[dict]:
    ordered = sorted(counter.items(), key=lambda item: (-item[1], str(item[0]).lower()))
    return [{"label": str(label), "total": int(total)} for label, total in ordered[:limit]]


def _extract_centro_group_char(codigo: str | None) -> str:
    value = str(codigo or "").strip().upper()
    if len(value) < 5:
        return ""
    return value[4]


@manutencao_bp.route("/dashboard", methods=["GET"])
@require_auth(("Manutenção", "Administrador"))
def manutencao_dashboard_data():
    sistema_id = (request.args.get("sistema_id") or "").strip()
    subsistema_id = (request.args.get("subsistema_id") or "").strip()
    centro_trabalho = (request.args.get("centro_trabalho") or "").strip().upper()
    data_inicio = _parse_date(request.args.get("data_inicio"))
    data_fim = _parse_date(request.args.get("data_fim"))
    if request.args.get("data_inicio") and data_inicio is None:
        return jsonify({"erro": "Parametro data_inicio invalido. Use YYYY-MM-DD."}), 400
    if request.args.get("data_fim") and data_fim is None:
        return jsonify({"erro": "Parametro data_fim invalido. Use YYYY-MM-DD."}), 400
    if data_inicio and data_fim and data_inicio > data_fim:
        return jsonify({"erro": "Parametro data_inicio deve ser menor ou igual a data_fim."}), 400

    try:
        supabase = _get_supabase_client()

        solicitacoes_res = (
            supabase.table("saf_solicitacoes")
            .select(
                "id, ticket_saf, titulo_falha, descricao_longa, numero_ocorrencia, prioridade, "
                "status, motivo_devolucao, motivo_cancelamento, criado_em, data_avaliacao, local_instalacao, local_instalacao_id, "
                "equipamento, equipamento_id, sistema_id, subsistema_id, sintoma_id, ccm_centro_trabalho, "
                "ccm_texto_breve_nota, ccm_texto_longo_nota, ccm_numero_nota, ccm_numero_ordem, "
                "avaliado_por, notificador_id, notificador_nome, notificador_area, anexo_evidencia_url, "
                "data_inicio_avaria, hora_inicio_avaria, via_numero, km_inicial, km_final, "
                "atualizado_sap, tipo_nota, qmnum_duplicata, "
                "saf_integracao_sap(qmnum, aufnr, numero_ordem_sap, tipo_nota, status_integracao)"
            )
            .order("criado_em", desc=True)
            .execute()
        )
        sistemas_res = (
            supabase.table("sistemas")
            .select("id, nome, codigo")
            .order("nome")
            .execute()
        )
        subsistemas_res = (
            supabase.table("subsistemas")
            .select("id, sistema_id, nome, codigo")
            .order("nome")
            .execute()
        )
        centros_trabalho_res = (
            supabase.table("centros_trabalho")
            .select("codigo, denominacao, ativo")
            .eq("ativo", True)
            .order("codigo")
            .execute()
        )
        sintomas_res = (
            supabase.table("sintomas_catalogo")
            .select("id, descricao")
            .execute()
        )
        estacoes_res = (
            supabase.table("estacoes")
            .select("id, linha, estacao")
            .execute()
        )
        frotas_res = (
            supabase.table("frotas_trens")
            .select("serie_trem, prefixo_trem")
            .execute()
        )
    except Exception:
        logger.exception("Erro ao consultar dados do dashboard de manutencao")
        return jsonify({"erro": "Não foi possivel consultar os dados do dashboard de manutencao."}), 500

    sistemas = [
        {
            "id": str(row.get("id")),
            "nome": str(row.get("nome") or "").strip(),
            "codigo": str(row.get("codigo") or "").strip().upper(),
        }
        for row in (sistemas_res.data or [])
        if row.get("id") is not None and str(row.get("nome") or "").strip()
    ]
    sistemas_by_id = {str(item["id"]): item["nome"] for item in sistemas}
    sistema_codigo_por_id = {str(item["id"]): str(item.get("codigo") or "").strip().upper() for item in sistemas}
    sistema_ids_validos = set(sistemas_by_id.keys())
    subsistema_nome_por_id = {
        str(row.get("id")): str(row.get("nome") or "").strip()
        for row in (subsistemas_res.data or [])
        if row.get("id") is not None and str(row.get("nome") or "").strip()
    }
    subsistemas_rows = [row for row in (subsistemas_res.data or []) if row.get("id") is not None]
    sintoma_nome_por_id = {
        str(row.get("id")): str(row.get("descricao") or "").strip()
        for row in (sintomas_res.data or [])
        if row.get("id") is not None and str(row.get("descricao") or "").strip()
    }
    estacao_por_id = {
        str(row.get("id")): {
            "linha": str(row.get("linha") or "").strip(),
            "estacao": str(row.get("estacao") or "").strip(),
        }
        for row in (estacoes_res.data or [])
        if row.get("id") is not None and str(row.get("estacao") or "").strip()
    }
    serie_por_prefixo = {
        str(row.get("prefixo_trem") or "").strip().upper(): str(row.get("serie_trem") or "").strip()
        for row in (frotas_res.data or [])
        if str(row.get("prefixo_trem") or "").strip() and str(row.get("serie_trem") or "").strip()
    }

    subsistemas_por_sistema: dict[str, list[dict]] = defaultdict(list)
    for sistema in sistemas:
        sid = str(sistema["id"])
        codigo_sistema = str(sistema.get("codigo") or "").strip().upper()
        relacionados: list[dict] = []
        for sub in subsistemas_rows:
            sub_id = str(sub.get("id") or "").strip()
            if not sub_id:
                continue
            sub_sistema_id = str(sub.get("sistema_id") or "").strip()
            sub_codigo = str(sub.get("codigo") or "").strip().upper()
            related = False
            if sub_sistema_id and sub_sistema_id == sid:
                related = True
            elif codigo_sistema and sub_codigo and sub_codigo.startswith(codigo_sistema):
                related = True
            if not related:
                continue
            relacionados.append({
                "id": sub_id,
                "nome": str(sub.get("nome") or "").strip() or f"Subsistema {sub_id}",
                "codigo": sub_codigo,
                "sistema_id": sub_sistema_id or sid,
            })
        relacionados.sort(key=lambda item: (item["nome"].lower(), item["id"]))
        subsistemas_por_sistema[sid] = relacionados

    group_char_to_system_id = {
        "R": "8",
        "E": "2",
        "A": "3",
        "T": "5",
        "C": "7",
        "S": "4",
        "M": "1",
        "V": "6",
    }
    centros_rows = []
    centros_por_sistema: dict[str, set[str]] = defaultdict(set)
    centro_denominacao_por_codigo: dict[str, str] = {}
    for row in (centros_trabalho_res.data or []):
        codigo = str(row.get("codigo") or "").strip().upper()
        if not codigo:
            continue
        denominacao = str(row.get("denominacao") or "").strip()
        centro_denominacao_por_codigo[codigo] = denominacao
        group_char = _extract_centro_group_char(codigo)
        mapped_system_id = group_char_to_system_id.get(group_char, "")
        if mapped_system_id and mapped_system_id in sistema_ids_validos:
            centros_por_sistema[mapped_system_id].add(codigo)
        centros_rows.append({
            "codigo": codigo,
            "denominacao": denominacao,
            "grupo_caractere": group_char,
            "sistema_id_mapeado": mapped_system_id if mapped_system_id in sistema_ids_validos else "",
        })

    solicitacoes = solicitacoes_res.data or []
    if data_inicio or data_fim:
        solicitacoes = [
            row for row in solicitacoes
            if _in_date_range(row.get("criado_em"), data_inicio, data_fim)
        ]

    def is_aberta(status: str) -> bool:
        return status in ("ABERTA", "EM_ANALISE", "DEVOLVIDA")

    def is_aprovada(status: str) -> bool:
        return status == "APROVADA"

    abertas = [row for row in solicitacoes if is_aberta(str(row.get("status") or "").strip().upper())]
    aprovadas = [row for row in solicitacoes if is_aprovada(str(row.get("status") or "").strip().upper())]

    ids_avaliadores = []
    for row in aprovadas:
        aval_id = str(row.get("avaliado_por") or "").strip()
        if aval_id and aval_id not in ids_avaliadores:
            ids_avaliadores.append(aval_id)

    nomes_por_id = {}
    if ids_avaliadores:
        try:
            usuarios_resp = (
                supabase.table("usuarios")
                .select("id, nome")
                .in_("id", ids_avaliadores)
                .execute()
            )
            for usuario in (usuarios_resp.data or []):
                uid = str(usuario.get("id") or "").strip()
                if uid:
                    nomes_por_id[uid] = str(usuario.get("nome") or "").strip()
        except Exception:
            logger.exception("Falha ao resolver nomes de avaliadores no dashboard de manutencao")

    if sistema_id:
        abertas = [row for row in abertas if str(row.get("sistema_id") or "") == sistema_id]
        aprovadas = [row for row in aprovadas if str(row.get("sistema_id") or "") == sistema_id]
    if subsistema_id:
        abertas = [row for row in abertas if str(row.get("subsistema_id") or "") == subsistema_id]
        aprovadas = [row for row in aprovadas if str(row.get("subsistema_id") or "") == subsistema_id]
    if centro_trabalho:
        abertas = [row for row in abertas if str(row.get("ccm_centro_trabalho") or "").strip().upper() == centro_trabalho]
        aprovadas = [row for row in aprovadas if str(row.get("ccm_centro_trabalho") or "").strip().upper() == centro_trabalho]

    centros_disponiveis_set = set()
    if sistema_id:
        centros_disponiveis_set.update(centros_por_sistema.get(sistema_id, set()))
    else:
        for codes in centros_por_sistema.values():
            centros_disponiveis_set.update(codes)
    centros_disponiveis = sorted(centros_disponiveis_set)
    centros_relacionados = centros_disponiveis if sistema_id else sorted({
        codigo for codigo in centro_denominacao_por_codigo.keys()
        if _extract_centro_group_char(codigo) in group_char_to_system_id
    })

    cont_estacoes = Counter()
    cont_frotas = Counter()
    cont_locais = Counter()
    cont_sintomas = Counter()
    cont_sistemas = Counter()
    cont_subsistemas = Counter()
    cont_centros = Counter()

    registros_aprovados = []
    for row in aprovadas:
        sistema_key = str(row.get("sistema_id") or "").strip()
        subsistema_key = str(row.get("subsistema_id") or "").strip()
        sintoma_key = str(row.get("sintoma_id") or "").strip()
        local_id_key = str(row.get("local_instalacao_id") or "").strip()
        local_nome = str(row.get("local_instalacao") or "").strip() or "Não informado"
        equip = str(row.get("equipamento") or "").strip()
        centro = str(row.get("ccm_centro_trabalho") or "").strip().upper() or "NÃO INFORMADO"

        estacao_info = estacao_por_id.get(local_id_key) or {}
        estacao_nome = estacao_info.get("estacao") or local_nome
        linha_nome = estacao_info.get("linha") or ""
        sintoma_nome = sintoma_nome_por_id.get(sintoma_key) or "Não informado"
        sistema_nome = sistemas_by_id.get(sistema_key) or "Não informado"
        subsistema_nome = subsistema_nome_por_id.get(subsistema_key) or "Não informado"
        frota_nome = serie_por_prefixo.get(equip.upper()) if equip else None
        frota_nome = frota_nome or "Não identificado"

        cont_estacoes[estacao_nome] += 1
        cont_frotas[frota_nome] += 1
        cont_locais[local_nome] += 1
        cont_sintomas[sintoma_nome] += 1
        cont_sistemas[sistema_nome] += 1
        cont_subsistemas[subsistema_nome] += 1
        cont_centros[centro] += 1

        registros_aprovados.append({
            **row,
            "sistema_nome": sistema_nome,
            "subsistema_nome": subsistema_nome,
            "sintoma_nome": sintoma_nome,
            "estacao_nome": estacao_nome,
            "_linha": linha_nome,
            "_estacao": estacao_nome,
            "_data_notificacao": _to_date(row.get("criado_em")).isoformat() if _to_date(row.get("criado_em")) else "",
            "_numero_ocorrencia": str(row.get("numero_ocorrencia") or "").strip(),
            "frota_nome": frota_nome,
            "centro_trabalho_nome": centro,
            "aprovador_nome": nomes_por_id.get(str(row.get("avaliado_por") or "").strip(), ""),
        })

    total_aprovadas = len(aprovadas)
    contribuicao_centro = []
    if total_aprovadas > 0:
        for item in _top_rows(cont_centros, limit=20):
            total_item = item["total"]
            contribuicao_centro.append({
                "label": item["label"],
                "total": total_item,
                "percentual": round((total_item / total_aprovadas) * 100, 2),
            })

    return jsonify({
        "totais": {
            "abertas": len(abertas),
            "aprovadas_ccm": total_aprovadas,
            "geral": len(abertas) + total_aprovadas,
        },
        "filtros": {
            "sistemas": sistemas,
            "subsistemas_por_sistema": subsistemas_por_sistema,
            "subsistemas_relacionados": subsistemas_por_sistema.get(sistema_id, []) if sistema_id else [],
            "centros_detalhes": centros_rows,
            "centros_disponiveis": centros_disponiveis,
            "centros_relacionados": centros_relacionados,
            "centros_por_sistema": {
                sid: sorted(list(centros))
                for sid, centros in centros_por_sistema.items()
            },
        },
        "agregados": {
            "estacoes_mais_afetadas": _top_rows(cont_estacoes),
            "frotas_mais_afetadas": _top_rows(cont_frotas),
            "locais_mais_afetados": _top_rows(cont_locais),
            "sintomas_mais_usados": _top_rows(cont_sintomas),
            "sistemas": _top_rows(cont_sistemas),
            "subsistemas": _top_rows(cont_subsistemas),
            "contribuicao_centros": contribuicao_centro,
        },
        "registros_aprovados": registros_aprovados,
        "filtros_aplicados": {
            "sistema_id": sistema_id,
            "subsistema_id": subsistema_id,
            "centro_trabalho": centro_trabalho,
            "data_inicio": data_inicio.isoformat() if data_inicio else "",
            "data_fim": data_fim.isoformat() if data_fim else "",
        },
    }), 200
