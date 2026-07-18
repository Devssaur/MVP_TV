import os
import logging
from flask import Blueprint, jsonify, request
from dotenv import load_dotenv
from supabase import Client, create_client
from routes.security import require_auth

load_dotenv()

dados_bp = Blueprint("dados_bp", __name__)
logger = logging.getLogger(__name__)


def _get_supabase_client() -> Client:
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        raise RuntimeError("Variaveis SUPABASE_URL e SUPABASE_KEY nao configuradas.")

    return create_client(supabase_url, supabase_key)


def _dist_sq(lat1, lng1, lat2, lng2):
    """Distância² em graus (suficiente para ordenação relativa dentro de uma cidade)."""
    if lat2 is None or lng2 is None:
        return None
    return (lat1 - lat2) ** 2 + (lng1 - lng2) ** 2


@dados_bp.route("/centros-trabalho", methods=["GET"])
@require_auth(("CCM", "Administrador"))
def listar_centros_trabalho():
    """Lista centros de trabalho ativos para validacao CCM."""
    try:
        supabase = _get_supabase_client()
        result = (
            supabase.table("centros_trabalho")
            .select("codigo, denominacao, ativo")
            .eq("ativo", True)
            .order("codigo")
            .execute()
        )
        centros = [
            {
                "codigo": str(r.get("codigo") or "").strip(),
                "denominacao": str(r.get("denominacao") or "").strip(),
            }
            for r in (result.data or [])
            if str(r.get("codigo") or "").strip()
        ]
        return jsonify({"centros": centros, "total": len(centros)}), 200
    except Exception:
        logger.exception('Erro ao listar centros de trabalho')
        return jsonify({'erro': 'Nao foi possivel consultar os centros de trabalho.'}), 500


@dados_bp.route("/locais", methods=["GET"])
@require_auth(("Solicitante", "CCM", "Administrador", "SIC"))
def listar_locais():
    """Lista locais de instalação ativos.
    Retorna 'id' = id_sap para compatibilidade com o seletor manual do formulário.
    """
    try:
        supabase = _get_supabase_client()
        categoria = (request.args.get("categoria") or "").strip().upper()

        if categoria == "MRO":
            result = (
                supabase.table("frotas_trens")
                .select("serie_trem")
                .order("serie_trem")
                .execute()
            )
            series = []
            seen = set()
            for row in result.data:
                serie = (row.get("serie_trem") or "").strip()
                if not serie or serie in seen:
                    continue
                seen.add(serie)
                series.append({
                    "id": serie,
                    "codigo": "",
                    "descricao": serie,
                    "lat": None,
                    "lng": None,
                })
            return jsonify({"locais": series, "total": len(series)}), 200

        if categoria == "VIA":
            result = (
                supabase.table("trechos_vias")
                .select("linha")
                .order("linha")
                .execute()
            )
            linhas = []
            seen = set()
            for row in result.data:
                linha = str(row.get("linha") or "").strip()
                if not linha or linha in seen:
                    continue
                seen.add(linha)
                linhas.append({
                    "id": linha,
                    "codigo": "",
                    "descricao": linha,
                    "lat": None,
                    "lng": None,
                })
            return jsonify({"locais": linhas, "total": len(linhas)}), 200

        result = (
            supabase.table("locais_instalacao")
            .select("id_sap, codigo, descricao, lat, lng")
            .eq("ativo", True)
            .order("descricao")
            .execute()
        )
        locais = [
            {
                "id":       r["id_sap"],
                "codigo":   r.get("codigo") or r.get("id_sap", ""),
                "descricao": r["descricao"],
                "lat":      r.get("lat"),
                "lng":      r.get("lng"),
            }
            for r in result.data
        ]
        return jsonify({"locais": locais, "total": len(locais)}), 200
    except Exception:
        logger.exception('Erro ao listar locais')
        return jsonify({'erro': 'Nao foi possivel consultar os dados mestres.'}), 500


@dados_bp.route("/equipamentos/<local_id_sap>", methods=["GET"])
@require_auth(("Solicitante", "CCM", "Administrador", "SIC"))
def listar_equipamentos_por_local(local_id_sap):
    """Equipamentos ativos de um local. 'id' = id_sap."""
    try:
        supabase = _get_supabase_client()
        categoria = (request.args.get("categoria") or "").strip().upper()

        if categoria == "MRO":
            result = (
                supabase.table("frotas_trens")
                .select("prefixo_trem")
                .eq("serie_trem", local_id_sap)
                .order("prefixo_trem")
                .execute()
            )
            equips = []
            seen = set()
            for row in result.data:
                prefixo = (row.get("prefixo_trem") or "").strip()
                if not prefixo or prefixo in seen:
                    continue
                seen.add(prefixo)
                equips.append({
                    "id": prefixo,
                    "codigo": prefixo,
                    "descricao": prefixo,
                    "grupo_catalogo": None,
                })
            return jsonify({"equipamentos": equips, "total": len(equips)}), 200

        if categoria == "VIA":
            result = (
                supabase.table("trechos_vias")
                .select("codigo_local, descricao")
                .eq("linha", local_id_sap)
                .order("descricao")
                .execute()
            )
            equips = []
            seen = set()
            for row in result.data:
                codigo_local = str(row.get("codigo_local") or "").strip()
                descricao = str(row.get("descricao") or "").strip()
                key = f"{codigo_local}|{descricao}"
                if not descricao or key in seen:
                    continue
                seen.add(key)
                equips.append({
                    "id": codigo_local or descricao,
                    "codigo": codigo_local,
                    "descricao": descricao,
                    "grupo_catalogo": None,
                })
            return jsonify({"equipamentos": equips, "total": len(equips)}), 200

        # Usa LIKE com prefixo para que selecionar um local raiz (ex.: TV11)
        # também retorne equipamentos de seus subsistemas (TV11-2, TV11-7, etc.)
        result = (
            supabase.table("equipamentos")
            .select("id_sap, codigo, descricao, grupo_catalogo")
            .like("local_id_sap", f"{local_id_sap}%")
            .eq("ativo", True)
            .order("descricao")
            .execute()
        )
        equips = [
            {
                "id":             r["id_sap"],
                "codigo":         r.get("codigo") or r.get("id_sap", ""),
                "descricao":      r["descricao"],
                "grupo_catalogo": r.get("grupo_catalogo"),
            }
            for r in result.data
        ]
        return jsonify({"equipamentos": equips, "total": len(equips)}), 200
    except Exception:
        logger.exception('Erro ao listar equipamentos por local=%s', local_id_sap)
        return jsonify({'erro': 'Nao foi possivel consultar os dados mestres.'}), 500


@dados_bp.route("/sintomas/<equipamento_id_sap>", methods=["GET"])
@require_auth(("Solicitante", "CCM", "Administrador", "SIC"))
def listar_sintomas_por_equipamento(equipamento_id_sap):
    """Retorna sintomas válidos para o tipo de equipamento (arborização SAP).

    Busca o grupo_catalogo do equipamento e filtra sintomas_catalogo pelo mesmo
    grupo (QMGRP). Ex.: escada rolante → grupo 'ME' → apenas sintomas mecânicos.
    Sem grupo_catalogo cadastrado, retorna todos os sintomas ativos.
    """
    try:
        supabase = _get_supabase_client()

        # Descobre o grupo de catálogo SAP do equipamento
        eq_res = (
            supabase.table("equipamentos")
            .select("grupo_catalogo")
            .eq("id_sap", equipamento_id_sap)
            .maybe_single()
            .execute()
        )
        grupo = (eq_res.data or {}).get("grupo_catalogo")

        sint_q = (
            supabase.table("sintomas_catalogo")
            .select("id, codigo, descricao, grupo, codigo_item")
            .eq("ativo", True)
        )
        if grupo:
            sint_q = sint_q.eq("grupo", grupo)

        result = sint_q.order("descricao").limit(200).execute()

        sintomas = [
            {
                "id":          r["id"],
                "codigo":      r.get("codigo", ""),
                "descricao":   r["descricao"],
                "grupo":       r.get("grupo"),
                "codigo_item": r.get("codigo_item"),
            }
            for r in result.data
        ]
        return jsonify({
            "sintomas":        sintomas,
            "total":           len(sintomas),
            "grupo_filtrado":  grupo,
        }), 200
    except Exception:
        logger.exception('Erro ao listar sintomas por equipamento=%s', equipamento_id_sap)
        return jsonify({'erro': 'Nao foi possivel consultar os dados mestres.'}), 500


@dados_bp.route("/estacoes", methods=["GET"])
@require_auth(("Solicitante", "CCM", "Administrador", "SIC"))
def listar_estacoes():
    """Lista estacoes da tabela estacoes.

    Query params:
      linha - filtro opcional pela coluna linha.
    """
    try:
        supabase = _get_supabase_client()
        linha = (request.args.get("linha") or "").strip()

        q = (
            supabase.table("estacoes")
            .select("id, linha, estacao, sigla")
            .order("linha")
            .order("estacao")
        )
        if linha:
            q = q.eq("linha", linha)

        result = q.execute()

        estacoes = [
            {
                "id": r.get("id"),
                "linha": str(r.get("linha") or "").strip(),
                "estacao": str(r.get("estacao") or "").strip(),
                    "sigla": str(r.get("sigla") or "").strip(),
            }
            for r in (result.data or [])
            if str(r.get("linha") or "").strip() and str(r.get("estacao") or "").strip()
        ]

        return jsonify({"estacoes": estacoes, "total": len(estacoes)}), 200
    except Exception:
        logger.exception('Erro ao listar estacoes')
        return jsonify({'erro': 'Nao foi possivel consultar os dados mestres.'}), 500


@dados_bp.route('/subsistemas', methods=['GET'])
@require_auth(("Solicitante", "CCM", "Administrador", "SIC"))
def listar_subsistemas():
    """Lista subsistemas por sistema_id para montagem dinamica do formulario SAF."""
    try:
        supabase = _get_supabase_client()
        sistema_id = request.args.get('sistema_id', type=int)

        q = (
            supabase.table('subsistemas')
            .select('id, sistema_id, codigo, nome')
            .order('sistema_id')
            .order('nome')
        )
        if sistema_id:
            q = q.eq('sistema_id', sistema_id)

        result = q.execute()
        subsistemas = [
            {
                'id': r.get('id'),
                'sistema_id': r.get('sistema_id'),
                'codigo': str(r.get('codigo') or '').strip(),
                'nome': str(r.get('nome') or '').strip(),
            }
            for r in (result.data or [])
            if r.get('id') and r.get('sistema_id')
        ]

        return jsonify({'subsistemas': subsistemas, 'total': len(subsistemas)}), 200
    except Exception:
        logger.exception('Erro ao listar subsistemas')
        return jsonify({'erro': 'Nao foi possivel consultar os subsistemas.'}), 500


@dados_bp.route('/sistemas-por-tipo', methods=['GET'])
@require_auth(("Solicitante", "CCM", "Administrador", "SIC"))
def listar_sistemas_por_tipo():
    """Lista sistemas permitidos por tipo de atendimento.

    Regras:
      - MRO: 1
      - ESTACAO: 2, 5, 7, 8
      - VIA: 3, 4, 6
    """
    tipo = (request.args.get('tipo') or '').strip().upper()
    ids_por_tipo = {
        'MRO': [1],
        'ESTACAO': [2, 5, 7, 8],
        'VIA': [3, 4, 6],
    }
    ids_permitidos = ids_por_tipo.get(tipo)
    if not ids_permitidos:
        return jsonify({'erro': 'Parametro tipo invalido.'}), 400

    try:
        supabase = _get_supabase_client()

        # Busca nomes da tabela sistemas para renderizar o dropdown de forma amigavel.
        sis_result = (
            supabase.table('sistemas')
            .select('id, nome, codigo')
            .in_('id', ids_permitidos)
            .order('id')
            .execute()
        )

        by_id = {int(r.get('id')): r for r in (sis_result.data or []) if r.get('id') is not None}
        sistemas = []
        for sis_id in ids_permitidos:
            row = by_id.get(int(sis_id), {})
            nome = str(row.get('nome') or f'Sistema {sis_id}').strip()
            codigo = str(row.get('codigo') or '').strip()
            sistemas.append({
                'id': int(sis_id),
                'nome': nome,
                'codigo': codigo,
                'label': nome,
            })

        return jsonify({'sistemas': sistemas, 'total': len(sistemas)}), 200
    except Exception:
        logger.exception('Erro ao listar sistemas por tipo=%s', tipo)
        return jsonify({'erro': 'Nao foi possivel consultar os sistemas.'}), 500


@dados_bp.route('/falhas', methods=['GET'])
@require_auth(("Solicitante", "CCM", "Administrador", "SIC"))
def listar_falhas_por_subsistema():
    """Lista falhas (tabela sintomas) filtradas por grupo_id.

    Fluxo:
      1) Descobre grupos (tabela grupos) do sistema_id informado.
      2) Opcionalmente tenta refinar pelo codigo do subsistema selecionado.
      3) Retorna sintomas vinculados aos grupo_id encontrados.
    """
    sistema_id = request.args.get('sistema_id', type=int)
    if not sistema_id:
        return jsonify({'erro': 'Parametro sistema_id obrigatorio.'}), 400

    try:
        supabase = _get_supabase_client()

        grupos_res = (
            supabase.table('grupos')
            .select('id, sistema_id, codigo, sintoma')
            .eq('sistema_id', sistema_id)
            .order('codigo')
            .execute()
        )
        grupos = grupos_res.data or []
        if not grupos:
            return jsonify({'falhas': [], 'total': 0}), 200

        grupo_ids = [g.get('id') for g in grupos if g.get('id')]
        if not grupo_ids:
            return jsonify({'falhas': [], 'total': 0}), 200

        sint_res = (
            supabase.table('sintomas')
            .select('id, descricao, grupo_id, ativo')
            .in_('grupo_id', grupo_ids)
            .eq('ativo', True)
            .order('descricao')
            .execute()
        )

        grupos_por_id = {str(g.get('id')): g for g in grupos}
        falhas = []
        for r in (sint_res.data or []):
            falha_id = r.get('id')
            desc = str(r.get('descricao') or '').strip()
            grp_id = r.get('grupo_id')
            if not falha_id or not desc:
                continue
            grp = grupos_por_id.get(str(grp_id), {})
            grp_codigo = str(grp.get('codigo') or '').strip()
            falhas.append({
                'id': falha_id,
                'descricao': desc,
                'grupo_id': grp_id,
                'grupo_codigo': grp_codigo,
                'label': f"{grp_codigo} - {desc}" if grp_codigo else desc,
            })

        return jsonify({'falhas': falhas, 'total': len(falhas)}), 200
    except Exception:
        logger.exception('Erro ao listar falhas por sistema/subsistema')
        return jsonify({'erro': 'Nao foi possivel consultar as falhas.'}), 500


@dados_bp.route('/opcoes-formulario', methods=['GET'])
@require_auth(("Solicitante", "CCM", "Administrador", "SIC"))
def listar_opcoes_formulario():
    """Retorna opcoes dinamicas do formulario 2.1 ligadas ao banco.

    Query params:
      tipo        - serie|trem|carro|trecho|via|tag|local
      linha       - filtro opcional para trechos/vias/tags
      serie       - filtro opcional para trem/carro
      trem        - filtro opcional para carro
      sistema_id  - filtro opcional para tags
      subsistema_id - filtro opcional para tags
    """
    tipo = (request.args.get('tipo') or '').strip().lower()
    linha = (request.args.get('linha') or '').strip()
    serie = (request.args.get('serie') or '').strip()
    trem = (request.args.get('trem') or '').strip()
    sistema_id = request.args.get('sistema_id', type=int)
    subsistema_id = request.args.get('subsistema_id', type=int)

    if tipo not in {'serie', 'trem', 'carro', 'trecho', 'via', 'tag', 'local', 'amv'}:
        return jsonify({'erro': 'Parametro tipo invalido.'}), 400

    try:
        supabase = _get_supabase_client()

        if tipo == 'serie':
            result = (
                supabase.table('frotas_trens')
                .select('serie_trem')
                .order('serie_trem')
                .execute()
            )
            valores = sorted({str(r.get('serie_trem') or '').strip() for r in (result.data or []) if str(r.get('serie_trem') or '').strip()})
            return jsonify({'opcoes': [{'value': v, 'label': v} for v in valores], 'total': len(valores)}), 200

        if tipo == 'trem':
            q = supabase.table('frotas_trens').select('prefixo_trem').order('prefixo_trem')
            if serie:
                q = q.eq('serie_trem', serie)
            result = q.execute()
            valores = sorted({str(r.get('prefixo_trem') or '').strip() for r in (result.data or []) if str(r.get('prefixo_trem') or '').strip()})
            return jsonify({'opcoes': [{'value': v, 'label': v} for v in valores], 'total': len(valores)}), 200

        if tipo == 'carro':
            # Compatibilidade: em alguns dumps existe a coluna carro_associado.
            valores = set()
            try:
                q = supabase.table('frotas_trens').select('carro_associado').order('carro_associado')
                if serie:
                    q = q.eq('serie_trem', serie)
                if trem:
                    q = q.eq('prefixo_trem', trem)
                result = q.execute()
                valores = {str(r.get('carro_associado') or '').strip() for r in (result.data or []) if str(r.get('carro_associado') or '').strip()}
            except Exception:
                logger.warning('Coluna carro_associado indisponivel em frotas_trens; retornando lista vazia.')
            ordered = sorted(valores)
            return jsonify({'opcoes': [{'value': v, 'label': v} for v in ordered], 'total': len(ordered)}), 200

        if tipo == 'trecho':
            q = supabase.table('trechos_vias').select('codigo_local, descricao').order('descricao')
            if linha:
                q = q.eq('linha', linha)
            result = q.execute()
            opcoes = []
            seen = set()
            for r in (result.data or []):
                value = str(r.get('codigo_local') or '').strip()
                label = str(r.get('descricao') or '').strip()
                if not value or not label:
                    continue
                if value in seen:
                    continue
                seen.add(value)
                opcoes.append({'value': value, 'label': label})
            return jsonify({'opcoes': opcoes, 'total': len(opcoes)}), 200

        if tipo == 'via':
            q = supabase.table('trechos_vias').select('codigo_local, linha, estacao_origem, estacao_destino').order('linha').order('estacao_origem')
            if linha:
                q = q.eq('linha', linha)
            result = q.execute()
            opcoes = []
            seen = set()
            for r in (result.data or []):
                value = str(r.get('codigo_local') or '').strip()
                origem = str(r.get('estacao_origem') or '').strip()
                destino = str(r.get('estacao_destino') or '').strip()
                linha_txt = str(r.get('linha') or '').strip()
                if not value or not origem or not destino:
                    continue
                if value in seen:
                    continue
                seen.add(value)
                label = f"Linha {linha_txt} - {origem} x {destino}" if linha_txt else f"{origem} x {destino}"
                opcoes.append({'value': value, 'label': label})
            return jsonify({'opcoes': opcoes, 'total': len(opcoes)}), 200

        if tipo == 'local':
            q = supabase.table('locais_instalacao').select('id_sap, descricao').eq('ativo', True).order('descricao')
            if linha:
                q = q.ilike('descricao', f"L{linha}%")
            result = q.execute()
            opcoes = []
            for r in (result.data or []):
                value = str(r.get('id_sap') or '').strip()
                label = str(r.get('descricao') or '').strip()
                if value and label:
                    opcoes.append({'value': value, 'label': label})
            return jsonify({'opcoes': opcoes, 'total': len(opcoes)}), 200

        if tipo == 'amv':
            q = supabase.table('amv_via').select('descricao').order('descricao')
            result = q.execute()
            opcoes = []
            seen = set()
            for r in (result.data or []):
                label = str(r.get('descricao') or '').strip()
                if not label:
                    continue
                if label in seen:
                    continue
                seen.add(label)
                opcoes.append({'value': label, 'label': label})
            return jsonify({'opcoes': opcoes, 'total': len(opcoes)}), 200

        # tipo == 'tag'
        q = supabase.table('equipamentos').select('id_sap, codigo, descricao, sistema_id, subsistema_id').eq('ativo', True).order('codigo')
        if sistema_id:
            q = q.eq('sistema_id', sistema_id)
        if subsistema_id:
            q = q.eq('subsistema_id', subsistema_id)
        if linha:
            q = q.ilike('local_id_sap', f"TV{linha}%")
        result = q.execute()
        opcoes = []
        seen = set()
        for r in (result.data or []):
            value = str(r.get('codigo') or r.get('id_sap') or '').strip()
            desc = str(r.get('descricao') or '').strip()
            if not value or value in seen:
                continue
            seen.add(value)
            label = f"{value} - {desc}" if desc else value
            opcoes.append({'value': value, 'label': label})
        return jsonify({'opcoes': opcoes, 'total': len(opcoes)}), 200

    except Exception:
        logger.exception('Erro ao listar opcoes dinamicas do formulario')
        return jsonify({'erro': 'Nao foi possivel consultar as opcoes do formulario.'}), 500


@dados_bp.route("/sugerir", methods=["GET"])
@require_auth(("Solicitante", "CCM", "Administrador", "SIC"))
def sugerir():
    """Busca inteligente por equipamentos/sintomas a partir de texto livre.

    Query params:
      q   — texto digitado pelo usuário (mín. 2 chars)
      lat — latitude GPS do usuário (opcional, para ordenação por proximidade)
      lng — longitude GPS do usuário (opcional)

    Quando lat/lng são fornecidos, equipamentos mais próximos aparecem primeiro.
    """
    q = (request.args.get("q") or "").strip()
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    categoria = (request.args.get("categoria") or "").strip().upper()
    has_gps = lat is not None and lng is not None

    if len(q) < 2:
        return jsonify({"sugestoes": []}), 200

    try:
        supabase = _get_supabase_client()
    except RuntimeError:
        logger.exception('Configuracao Supabase ausente em sugerir')
        return jsonify({'erro': 'Nao foi possivel consultar os dados mestres.'}), 500

    try:
        pattern = f"%{q}%"
        sugestoes = []
        seen_equip_ids = set()

        if categoria == "MRO":
            trem_res = (
                supabase.table("frotas_trens")
                .select("serie_trem, prefixo_trem")
                .ilike("prefixo_trem", pattern)
                .order("prefixo_trem")
                .limit(12)
                .execute()
            )

            query_norm = q.lower()
            itens = []
            for row in trem_res.data:
                prefixo = (row.get("prefixo_trem") or "").strip()
                serie = (row.get("serie_trem") or "").strip()
                if not prefixo:
                    continue
                score = 2
                pref_norm = prefixo.lower()
                if pref_norm == query_norm:
                    score = 0
                elif pref_norm.startswith(query_norm):
                    score = 1
                itens.append({
                    "tipo": "trem",
                    "equip_id": prefixo,
                    "equip_nome": prefixo,
                    "local_id": serie,
                    "local_nome": serie,
                    "sintoma_id": None,
                    "sintoma_nome": None,
                    "_score": score,
                })

            itens.sort(key=lambda x: (x["_score"], x["equip_nome"]))
            for item in itens:
                item.pop("_score", None)
            return jsonify({"sugestoes": itens[:8]}), 200

        if categoria == "VIA":
            via_res = (
                supabase.table("trechos_vias")
                .select("codigo_local, linha, descricao")
                .ilike("descricao", pattern)
                .order("descricao")
                .limit(12)
                .execute()
            )
            query_norm = q.lower()
            itens = []
            for row in via_res.data:
                descricao = str(row.get("descricao") or "").strip()
                linha = str(row.get("linha") or "").strip()
                codigo_local = str(row.get("codigo_local") or "").strip()
                if not descricao:
                    continue
                desc_norm = descricao.lower()
                score = 2
                if desc_norm == query_norm:
                    score = 0
                elif desc_norm.startswith(query_norm):
                    score = 1
                itens.append({
                    "tipo": "via",
                    "equip_id": codigo_local or descricao,
                    "equip_nome": descricao,
                    "local_id": codigo_local or descricao,
                    "local_nome": f"Linha {linha}" if linha else "",
                    "sintoma_id": None,
                    "sintoma_nome": None,
                    "_score": score,
                })

            itens.sort(key=lambda x: (x["_score"], x["equip_nome"]))
            for item in itens:
                item.pop("_score", None)
            return jsonify({"sugestoes": itens[:8]}), 200

        # Com GPS buscamos mais resultados para depois ordenar por distância
        limit_equip = 30 if has_gps else 8

        # 1. Busca equipamentos pelo nome
        equip_res = (
            supabase.table("equipamentos")
            .select("id_sap, codigo, descricao, local_id_sap")
            .ilike("descricao", pattern)
            .eq("ativo", True)
            .limit(limit_equip)
            .execute()
        )

        # Coleta ids de locais para busca em lote (evita N+1)
        local_ids = list({e["local_id_sap"] for e in equip_res.data if e.get("local_id_sap")})
        locais_map = {}
        if local_ids:
            loc_batch = (
                supabase.table("locais_instalacao")
                .select("id_sap, codigo, descricao, lat, lng")
                .in_("id_sap", local_ids)
                .execute()
            )
            locais_map = {r["id_sap"]: r for r in loc_batch.data}

        for e in equip_res.data:
            local = locais_map.get(e.get("local_id_sap") or "", {})
            dist = _dist_sq(lat, lng, local.get("lat"), local.get("lng")) if has_gps else None
            seen_equip_ids.add(e["id_sap"])
            sugestoes.append({
                "tipo":        "equip",
                "equip_id":    e["id_sap"],
                "equip_nome":  e["descricao"],
                "local_id":    local.get("id_sap") or e.get("local_id_sap"),
                "local_nome":  local.get("descricao", ""),
                "sintoma_id":  None,
                "sintoma_nome": None,
                "_dist":       dist,
            })

        # 2. Busca locais pelo nome — retorna seus equipamentos
        if len(sugestoes) < 4:
            local_res = (
                supabase.table("locais_instalacao")
                .select("id_sap, codigo, descricao, lat, lng")
                .ilike("descricao", pattern)
                .eq("ativo", True)
                .limit(4)
                .execute()
            )
            for loc in local_res.data:
                dist = _dist_sq(lat, lng, loc.get("lat"), loc.get("lng")) if has_gps else None
                equips_loc = (
                    supabase.table("equipamentos")
                    .select("id_sap, descricao")
                    .eq("local_id_sap", loc["id_sap"])
                    .eq("ativo", True)
                    .limit(4)
                    .execute()
                )
                for e in equips_loc.data:
                    if e["id_sap"] not in seen_equip_ids:
                        seen_equip_ids.add(e["id_sap"])
                        sugestoes.append({
                            "tipo":        "equip",
                            "equip_id":    e["id_sap"],
                            "equip_nome":  e["descricao"],
                            "local_id":    loc["id_sap"],
                            "local_nome":  loc["descricao"],
                            "sintoma_id":  None,
                            "sintoma_nome": None,
                            "_dist":       dist,
                        })

        # 3. Busca sintomas cujo nome corresponde ao texto
        sint_res = (
            supabase.table("sintomas_catalogo")
            .select("id, descricao")
            .ilike("descricao", pattern)
            .eq("ativo", True)
            .limit(4)
            .execute()
        )
        for s in sint_res.data:
            sugestoes.append({
                "tipo":        "sintoma",
                "equip_id":    None,
                "equip_nome":  None,
                "local_id":    None,
                "local_nome":  None,
                "sintoma_id":  s["id"],
                "sintoma_nome": s["descricao"],
                "_dist":       None,
            })

        # Ordena equipamentos por proximidade GPS; sintomas sempre no final
        if has_gps:
            equip_sugs = [s for s in sugestoes if s["tipo"] == "equip"]
            other_sugs = [s for s in sugestoes if s["tipo"] != "equip"]
            equip_sugs.sort(
                key=lambda x: x["_dist"] if x["_dist"] is not None else float("inf")
            )
            sugestoes = equip_sugs[:6] + other_sugs[:2]

        # Remove campo interno de distância antes de retornar
        for s in sugestoes:
            s.pop("_dist", None)

        return jsonify({"sugestoes": sugestoes[:8]}), 200

    except Exception:
        logger.exception('Erro ao gerar sugestoes')
        return jsonify({'erro': 'Nao foi possivel consultar os dados mestres.'}), 500
