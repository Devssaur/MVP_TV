-- ============================================================
-- Migration: campos do popup de aceite CCM (nota SAP manual)
--
-- Objetivo:
-- 1) Persistir texto breve e texto longo montados pelo CCM.
-- 2) Persistir numero da nota e numero da ordem digitados no popup.
-- 3) Persistir centro de trabalho escolhido no popup.
-- ============================================================

-- Campos adicionais na solicitacao (inclui duplicadas)
ALTER TABLE public.saf_solicitacoes
  ADD COLUMN IF NOT EXISTS ccm_texto_breve_nota text,
  ADD COLUMN IF NOT EXISTS ccm_texto_longo_nota text,
  ADD COLUMN IF NOT EXISTS ccm_numero_nota text,
  ADD COLUMN IF NOT EXISTS ccm_numero_ordem text,
  ADD COLUMN IF NOT EXISTS ccm_centro_trabalho text;

-- Campos adicionais no espelho de integracao SAP
ALTER TABLE public.saf_integracao_sap
  ADD COLUMN IF NOT EXISTS texto_breve_nota text,
  ADD COLUMN IF NOT EXISTS texto_longo_nota text;

-- Indices para consultas na fila CCM
CREATE INDEX IF NOT EXISTS idx_saf_solicitacoes_ccm_numero_nota
  ON public.saf_solicitacoes(ccm_numero_nota);

CREATE INDEX IF NOT EXISTS idx_saf_solicitacoes_ccm_numero_ordem
  ON public.saf_solicitacoes(ccm_numero_ordem);
