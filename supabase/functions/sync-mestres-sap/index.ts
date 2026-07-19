// Supabase Edge Function: sync-mestres-sap
// Sincroniza Locais de Instalação e Equipamentos do SAP para o Supabase.
// Executada automaticamente a cada 2 minutos via pg_cron (ver migrations/cron_sync_mestres.sql).
//
// Variáveis de ambiente necessárias (configure em Supabase > Project Settings > Edge Functions):
//   SAP_BASE_URL, SAP_USER, SAP_PASSWORD, SAP_CLIENT
//   SAP_ENDPOINT_LOCAIS (opcional — substitui o endpoint padrão)
//   SAP_ENDPOINT_EQUIPAMENTOS (opcional)
//   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (injetadas automaticamente pelo Supabase)

import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// ──────────────────────────────────────────────────────────────
// Configuração
// ──────────────────────────────────────────────────────────────

const SAP_BASE_URL  = (Deno.env.get("SAP_BASE_URL")  ?? "").replace(/\/$/, "");
const SAP_USER      = Deno.env.get("SAP_USER")      ?? "";
const SAP_PASSWORD  = Deno.env.get("SAP_PASSWORD")  ?? "";
const SAP_CLIENT    = Deno.env.get("SAP_CLIENT")    ?? "100";

const SUPABASE_URL         = Deno.env.get("SUPABASE_URL")              ?? "";
const SUPABASE_SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

// ──────────────────────────────────────────────────────────────
// Helper: cabeçalhos SAP (Basic Auth + sap-client)
// ──────────────────────────────────────────────────────────────

function sapHeaders(): Record<string, string> {
  const token = btoa(`${SAP_USER}:${SAP_PASSWORD}`);
  return {
    "Authorization": `Basic ${token}`,
    "sap-client": SAP_CLIENT,
    "Accept": "application/json",
    "Content-Type": "application/json",
  };
}

// ──────────────────────────────────────────────────────────────
// Helper: GET no SAP Gateway
// ──────────────────────────────────────────────────────────────

async function sapGet(path: string): Promise<unknown> {
  const customUrl = Deno.env.get(
    path.includes("FunctionalLocation") ? "SAP_ENDPOINT_LOCAIS" : "SAP_ENDPOINT_EQUIPAMENTOS"
  );
  const url = customUrl ?? `${SAP_BASE_URL}${path}`;
  const res = await fetch(url, { headers: sapHeaders() });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`SAP GET ${url} → ${res.status}: ${body}`);
  }
  return res.json();
}

// ──────────────────────────────────────────────────────────────
// Lógica principal
// ──────────────────────────────────────────────────────────────

serve(async (_req) => {
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);
  const now = new Date().toISOString();
  let locaisSync = 0;
  let equipSync  = 0;

  try {
    // ── 1. Locais de Instalação (TPLNR / IL03) ──────────────────
    const locaisData = await sapGet(
      "/sap/opu/odata/sap/API_FUNCTLOCATION_SRV/FunctionalLocation" +
      "?$select=FunctionalLocation,FunctionalLocationName" +
      "&$filter=IsActive%20eq%20true&$format=json"
    ) as { d?: { results?: Array<{ FunctionalLocation: string; FunctionalLocationName: string }> } };

    const locais = locaisData?.d?.results ?? [];
    const codigosLocaisSAP = new Set<string>();

    for (const item of locais) {
      const codigo    = item.FunctionalLocation;
      const descricao = item.FunctionalLocationName ?? "";
      if (!codigo) continue;
      codigosLocaisSAP.add(codigo);

      await supabase.from("locais_instalacao").upsert(
        { id_sap: codigo, codigo, descricao, ativo: true, sincronizado_em: now },
        { onConflict: "id_sap" }
      );
      locaisSync++;
    }

    // Desativa locais que não vieram mais do SAP
    if (codigosLocaisSAP.size > 0) {
      await supabase.from("locais_instalacao")
        .update({ ativo: false })
        .not("codigo", "in", `(${[...codigosLocaisSAP].map(c => `'${c}'`).join(",")})`)
        .eq("ativo", true);
    }

    // ── 2. Equipamentos (EQUNR / IE03) ──────────────────────────
    const equipData = await sapGet(
      "/sap/opu/odata/sap/API_EQUIPMENT_SRV/Equipment" +
      "?$select=Equipment,EquipmentName,FunctionalLocation&$format=json"
    ) as { d?: { results?: Array<{ Equipment: string; EquipmentName: string; FunctionalLocation: string }> } };

    const equipamentos = equipData?.d?.results ?? [];
    const codigosEquipSAP = new Set<string>();

    for (const item of equipamentos) {
      const codigo    = item.Equipment;
      const descricao = item.EquipmentName ?? "";
      const tplnr     = item.FunctionalLocation;
      if (!codigo) continue;
      codigosEquipSAP.add(codigo);

      // Busca ID do local no cache
      let localIdSap: string | null = null;
      if (tplnr) {
        const { data } = await supabase
          .from("locais_instalacao")
          .select("id_sap")
          .eq("id_sap", tplnr)
          .maybeSingle();
        localIdSap = data?.id_sap ?? null;
      }

      await supabase.from("equipamentos").upsert(
        { id_sap: codigo, codigo, descricao, local_id_sap: localIdSap ?? tplnr ?? "", ativo: true, sincronizado_em: now },
        { onConflict: "id_sap" }
      );
      equipSync++;
    }

    // Desativa equipamentos que não vieram mais do SAP
    if (codigosEquipSAP.size > 0) {
      await supabase.from("equipamentos")
        .update({ ativo: false })
        .not("codigo", "in", `(${[...codigosEquipSAP].map(c => `'${c}'`).join(",")})`)
        .eq("ativo", true);
    }

    // ── 3. Sintomas / Catálogo Tipo C (QMGRP + QMCOD) ───────────
    const sintEndpoint = Deno.env.get("SAP_ENDPOINT_SINTOMAS") ??
      SAP_BASE_URL +
      "/sap/opu/odata/sap/API_MAINTNOTIFICATION/MaintNotifCatalog" +
      "?$filter=CatalogType%20eq%20%27C%27" +
      "&$select=CodeGroup,Code,CodeGroupDescription&$format=json";

    const sintData = await fetch(sintEndpoint, { headers: sapHeaders() });
    let sintSync = 0;

    if (sintData.ok) {
      const sintJson = await sintData.json() as {
        d?: { results?: Array<{ CodeGroup: string; Code: string; CodeGroupDescription: string }> }
      };
      const sintomas = sintJson?.d?.results ?? [];
      const codigosSintSAP = new Set<string>();

      for (const item of sintomas) {
        const grupo      = item.CodeGroup ?? "";
        const codigoItem = item.Code ?? "";
        const descricao  = item.CodeGroupDescription ?? "";
        if (!grupo || !codigoItem) continue;
        const codigo = `${grupo}-${codigoItem}`;
        codigosSintSAP.add(codigo);

        await supabase.from("sintomas_catalogo").upsert(
          { codigo, grupo, codigo_item: codigoItem, descricao, tipo_catalogo: "C", ativo: true, sincronizado_em: now },
          { onConflict: "codigo" }
        );
        sintSync++;
      }

      // Desativa sintomas que não vieram mais do SAP
      if (codigosSintSAP.size > 0) {
        await supabase.from("sintomas_catalogo")
          .update({ ativo: false })
          .not("codigo", "in", `(${[...codigosSintSAP].map(c => `'${c}'`).join(",")})`)
          .eq("ativo", true);
      }
    }

    // ── 4. Registra log de auditoria ─────────────────────────────
    await supabase.from("logs_auditoria").insert({
      evento: "SYNC_MESTRES_SAP",
      payload: { locais: locaisSync, equipamentos: equipSync, sintomas: sintSync, em: now },
    });

    return new Response(
      JSON.stringify({ locais: locaisSync, equipamentos: equipSync, sintomas: sintSync }),
      { headers: { "Content-Type": "application/json" } }
    );

  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("sync-mestres-sap error:", msg);

    try {
      await supabase.from("logs_auditoria").insert({
        evento: "SYNC_MESTRES_SAP_ERRO",
        payload: { erro: msg, em: now },
      });
    } catch (_) { /* silencia erro de log */ }

    return new Response(
      JSON.stringify({ erro: msg }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
});
