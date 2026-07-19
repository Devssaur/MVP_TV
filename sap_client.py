"""
Cliente HTTP para o SAP Gateway (API_MAINTNOTIFICATION / OData REST).

Centraliza todas as chamadas ao SAP para facilitar manutenção e testes.
Configurado via variáveis de ambiente (.env):
  SAP_BASE_URL, SAP_USER, SAP_PASSWORD, SAP_CLIENT, SAP_VERIFY_SSL
  SAP_ENDPOINT_CRIAR_NOTA, SAP_ENDPOINT_CANCELAR_NOTA,
  SAP_ENDPOINT_CONSULTAR_NOTA, SAP_ENDPOINT_LOCAIS,
  SAP_ENDPOINT_EQUIPAMENTOS, SAP_ENDPOINT_SINTOMAS
"""
import os
import base64
import logging
import random
import re
import requests
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Modo Mock (SAP_MOCK_MODE=true) — para testes sem SAP real
# ──────────────────────────────────────────────────────────────

def _is_mock() -> bool:
    """Retorna True se SAP_MOCK_MODE=true no .env (qualquer capitalização)."""
    return os.environ.get("SAP_MOCK_MODE", "false").strip().lower() in ("true", "1", "yes")


def _mock_qmnum() -> str:
    """Gera um número de nota SAP fictício (12 dígitos, começa com 1)."""
    return f"1{random.randint(10 ** 10, 10 ** 11 - 1)}"


# ──────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────

def _base_url() -> str:
    base = (os.environ.get("SAP_BASE_URL", "") or "").strip().rstrip("/")
    if not base:
        return ""

    # Permite configurar apenas host (ex.: sap.empresa.com) e assume HTTPS.
    if not urlparse(base).scheme:
        base = f"https://{base.lstrip('/')}"
    return base


def _headers() -> dict:
    user = os.environ.get("SAP_USER", "")
    pwd  = os.environ.get("SAP_PASSWORD", "")
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "sap-client": os.environ.get("SAP_CLIENT", "100"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _verify_ssl() -> bool:
    return os.environ.get("SAP_VERIFY_SSL", "false").strip().lower() not in ("false", "0", "no")


def _endpoint(env_key: str, default_path: str) -> str:
    custom = os.environ.get(env_key, "").strip()
    base = _base_url()

    def _absolutize(url_or_path: str) -> str:
        parsed = urlparse(url_or_path)
        if parsed.scheme:
            return url_or_path
        if not base:
            raise RuntimeError(
                f"Configuracao SAP invalida: {env_key}='{url_or_path}' sem esquema e SAP_BASE_URL ausente."
            )
        if url_or_path.startswith("/"):
            return f"{base}{url_or_path}"
        return f"{base}/{url_or_path}"

    if custom:
        return _absolutize(custom)
    return _absolutize(default_path)


# ──────────────────────────────────────────────────────────────
# Mapeamento de prioridade SAF → SAP (PRIOK)
# ──────────────────────────────────────────────────────────────

_PRIORIDADE_MAP = {
    "CRITICA": "1",
    "ALTA":    "2",
    "MEDIA":   "3",
    "BAIXA":   "4",
}


def _one_line(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _extract_linha(saf: dict) -> str:
    linha = _one_line(saf.get("linha") or "")
    if linha:
        return linha

    local = _one_line(saf.get("local_instalacao") or "")
    if not local:
        return ""

    match = re.search(r"(?:^|\s)L(?:inha)?\s*(\d{1,2})(?:\b|\s)", local, flags=re.IGNORECASE)
    if match and match.group(1):
        return match.group(1)
    return ""


def _format_long_text_sap(saf: dict) -> str:
    """Monta o Texto Longo SAP com rastreabilidade e campos em linhas separadas."""
    numero_saf = _one_line(saf.get("ticket_saf") or saf.get("id") or "")
    numero_ocorrencia = _one_line(
        saf.get("numero_ocorrencia")
        or saf.get("ocorrencia_numero")
        or saf.get("id_ocorrencia")
        or saf.get("id")
        or "NAO INFORMADO"
    )

    linhas = [
        f"Numero da SAF: {numero_saf or 'NAO INFORMADO'}",
        f"Numero da Ocorrencia: {numero_ocorrencia}",
        "Criador sistemico: S-SIGO-TRIVIA",
    ]

    linha = _extract_linha(saf)
    local = _one_line(saf.get("local_instalacao"))
    equipamento = _one_line(saf.get("equipamento"))
    sintoma = _one_line(saf.get("sintoma_descricao") or saf.get("sintoma") or "")
    descricao = _one_line(saf.get("descricao_longa") or saf.get("descricao_falha") or "")

    campos = [
        ("Linha", linha),
        ("Centro de Trabalho", _one_line(saf.get("centro_trabalho"))),
        ("Local", local),
        ("Equipamento", equipamento),
        ("Sintoma", sintoma),
        ("Prioridade", _one_line(saf.get("prioridade"))),
        ("Data inicio avaria", _one_line(saf.get("data_inicio_avaria"))),
        ("Hora inicio avaria", _one_line(saf.get("hora_inicio_avaria"))),
        ("Notificador", _one_line(saf.get("notificador_nome"))),
        ("Area", _one_line(saf.get("notificador_area"))),
        ("Descricao", descricao),
    ]

    for nome, valor in campos:
        if valor:
            linhas.append(f"{nome}: {valor}")

    return "\n".join(linhas)


# ──────────────────────────────────────────────────────────────
# CRIAR NOTA DE MANUTENÇÃO  (BAPI_ALM_NOTIF_CREATE equivalente)
# Endpoint: POST /sap/opu/odata/sap/API_MAINTNOTIFICATION/MaintenanceNotification
# ──────────────────────────────────────────────────────────────

def sap_criar_nota(saf: dict) -> dict:
    """
    Cria Nota de Manutenção no SAP a partir dos dados de uma SAF.

    Parâmetros esperados em `saf` (colunas de saf_solicitacoes + lookup de códigos SAP):
      tipo_nota            – QMART (YE=Corretiva Emergencial / YP=Corretiva Programada), default YP
      titulo_falha         – texto curto, max 40 chars (QMTXT)
      tplnr                – código técnico do Local de Instalação (TPLNR)
      equnr                – código técnico do Equipamento (EQUNR)
      qmgrp                – grupo do sintoma (QMGRP), opcional
      qmcod                – código do sintoma (QMCOD), opcional
      prioridade           – "CRITICA"|"ALTA"|"MEDIA"|"BAIXA"
      data_inicio_avaria   – date string YYYY-MM-DD (AUSVN / STRMN)
      hora_inicio_avaria   – time string HH:MM:SS  (AUZTV / STRUR)
      notificador_nome     – nome do usuário (QMNAM)
      descricao_longa      – texto livre (LongText)

    Retorna dict com chaves:
      qmnum  – número da nota criada (QMNUM)
      raw    – resposta bruta do SAP
    """
    url = _endpoint(
        "SAP_ENDPOINT_CRIAR_NOTA",
        "/sap/opu/odata/sap/API_MAINTNOTIFICATION/MaintenanceNotification",
    )

    prioridade_sap = _PRIORIDADE_MAP.get(
        str(saf.get("prioridade", "MEDIA")).upper(), "3"
    )

    # Códigos técnicos SAP — devem ser resolvidos antes de chamar esta função
    tplnr = (saf.get("tplnr") or saf.get("local_instalacao") or "").strip()
    equnr = (saf.get("equnr") or saf.get("equipamento") or "").strip()

    # Formata data/hora no formato SAP OData (/Date(ms)/ ou YYYY-MM-DD)
    data_avaria = str(saf.get("data_inicio_avaria") or "")
    hora_avaria = str(saf.get("hora_inicio_avaria") or "")

    payload = {
        "NotificationType":     saf.get("tipo_nota", "YP"),          # QMART
        "MaintNotifBrfTxt":     (saf.get("titulo_falha") or "")[:40], # QMTXT
        "FunctionalLocation":   tplnr,                                # TPLNR
        "Equipment":            equnr,                                 # EQUNR
        "Priority":             prioridade_sap,                        # PRIOK
        "MalfunctionStartDate": data_avaria,                          # AUSVN
        "MalfunctionStartTime": hora_avaria,                          # AUZTV
        "RequiredStartDate":    data_avaria,                          # STRMN
        "RequiredStartTime":    hora_avaria,                          # STRUR
        "ReportedByUser":       (saf.get("notificador_nome") or "")[:12],  # QMNAM (max 12)
        "LongText":             _format_long_text_sap(saf),
    }

    # Sintoma/Dano: adiciona apenas se ambos os campos estiverem preenchidos (QMGRP + QMCOD)
    qmgrp = (saf.get("qmgrp") or "").strip()
    qmcod = (saf.get("qmcod") or "").strip()
    if qmgrp and qmcod:
        payload["CatalogProfile"] = qmgrp   # QMGRP
        payload["CodeGroup"]      = qmgrp
        payload["Code"]           = qmcod   # QMCOD

    if _is_mock():
        qmnum_mock = _mock_qmnum()
        logger.warning("[MOCK] sap_criar_nota — SAP real não chamado. QMNUM fictício: %s", qmnum_mock)
        return {"qmnum": qmnum_mock, "raw": {"mock": True, "payload_enviado": payload}}

    logger.info("SAP criar_nota → %s | payload=%s", url, payload)

    resp = requests.post(
        url,
        json=payload,
        headers=_headers(),
        timeout=30,
        verify=_verify_ssl(),
    )
    resp.raise_for_status()
    data = resp.json()

    # SAP retorna o número da nota em diferentes campos dependendo do serviço
    qmnum = (
        data.get("d", {}).get("MaintenanceNotification")
        or data.get("d", {}).get("NotificationNo")
        or data.get("MaintenanceNotification")
        or data.get("NotificationNo")
        or data.get("qmnum")
    )

    return {"qmnum": qmnum, "raw": data}


# ──────────────────────────────────────────────────────────────
# CANCELAR NOTA  (BAPI_ALM_NOTIF_CLOSE equivalente)
# Endpoint: POST .../MaintenanceNotification('<qmnum>')/Cancel
# ──────────────────────────────────────────────────────────────

def sap_cancelar_nota(qmnum: str) -> dict:
    """
    Cancela Nota de Manutenção no SAP (seta status CANCL).
    Levanta exceção em caso de falha para garantir transação atômica.
    """
    url = _endpoint(
        "SAP_ENDPOINT_CANCELAR_NOTA",
        f"/sap/opu/odata/sap/API_MAINTNOTIFICATION/MaintenanceNotification('{qmnum}')/Cancel",
    )

    if _is_mock():
        logger.warning("[MOCK] sap_cancelar_nota — SAP real não chamado. qmnum=%s", qmnum)
        return {"mock": True, "qmnum": qmnum, "status": "CANCL"}

    logger.info("SAP cancelar_nota → %s | qmnum=%s", url, qmnum)

    resp = requests.post(
        url,
        headers=_headers(),
        timeout=30,
        verify=_verify_ssl(),
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}


# ──────────────────────────────────────────────────────────────
# CONSULTAR NOTA  (leitura de status e AUFNR)
# Endpoint: GET .../MaintenanceNotification('<qmnum>')
# ──────────────────────────────────────────────────────────────

def sap_consultar_nota(qmnum: str) -> dict:
    """
    Retorna dados de uma Nota do SAP: status (JEST-STAT) e ordem vinculada (AUFNR).
    """
    url = _endpoint(
        "SAP_ENDPOINT_CONSULTAR_NOTA",
        f"/sap/opu/odata/sap/API_MAINTNOTIFICATION/MaintenanceNotification('{qmnum}')?$format=json",
    )

    if _is_mock():
        logger.warning("[MOCK] sap_consultar_nota — SAP real não chamado. qmnum=%s", qmnum)
        return {
            "MaintenanceNotification": qmnum,
            "MaintNotifProcessingPhase": "NOPR",  # Em processamento
            "OrderID": "",
            "mock": True,
        }

    resp = requests.get(
        url,
        headers=_headers(),
        timeout=30,
        verify=_verify_ssl(),
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("d", data)


# ──────────────────────────────────────────────────────────────
# LISTAR LOCAIS DE INSTALAÇÃO  (IL03 / API_FUNCTLOCATION_SRV)
# ──────────────────────────────────────────────────────────────

def sap_listar_locais() -> list:
    """
    Retorna lista de Locais de Instalação ativos do SAP (TPLNR).
    """
    url = _endpoint(
        "SAP_ENDPOINT_LOCAIS",
        (
            "/sap/opu/odata/sap/API_FUNCTLOCATION_SRV/FunctionalLocation"
            "?$select=FunctionalLocation,FunctionalLocationName"
            "&$filter=IsActive%20eq%20true&$format=json"
        ),
    )

    resp = requests.get(
        url,
        headers=_headers(),
        timeout=60,
        verify=_verify_ssl(),
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("d", {}).get("results", [])


# ──────────────────────────────────────────────────────────────
# LISTAR EQUIPAMENTOS  (IE03 / API_EQUIPMENT_SRV)
# ──────────────────────────────────────────────────────────────

def sap_listar_equipamentos(tplnr: str = None) -> list:
    """
    Retorna Equipamentos do SAP (EQUNR), opcionalmente filtrados por Local (TPLNR).
    """
    filtro = f"$filter=FunctionalLocation%20eq%20%27{tplnr}%27&" if tplnr else ""
    url = _endpoint(
        "SAP_ENDPOINT_EQUIPAMENTOS",
        (
            f"/sap/opu/odata/sap/API_EQUIPMENT_SRV/Equipment"
            f"?{filtro}$select=Equipment,EquipmentName,FunctionalLocation&$format=json"
        ),
    )

    resp = requests.get(
        url,
        headers=_headers(),
        timeout=60,
        verify=_verify_ssl(),
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("d", {}).get("results", [])


# ──────────────────────────────────────────────────────────────
# LISTAR SINTOMAS  (Catálogo Tipo C / API_MAINTNOTIFICATION)
# ──────────────────────────────────────────────────────────────

def sap_listar_sintomas(equnr: str = None) -> list:
    """
    Retorna Catálogo Tipo C (Sintomas/Danos) do SAP.
    Opcionalmente filtrado pelo Equipamento para evitar erros na BAPI.
    """
    filtro = f"$filter=Equipment%20eq%20%27{equnr}%27%20and%20" if equnr else "$filter="
    url = _endpoint(
        "SAP_ENDPOINT_SINTOMAS",
        (
            "/sap/opu/odata/sap/API_MAINTNOTIFICATION/MaintNotifCatalog"
            f"?{filtro}CatalogType%20eq%20%27C%27"
            "&$select=CodeGroup,Code,CodeGroupDescription&$format=json"
        ),
    )

    resp = requests.get(
        url,
        headers=_headers(),
        timeout=30,
        verify=_verify_ssl(),
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("d", {}).get("results", [])
