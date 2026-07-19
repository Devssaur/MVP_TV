-- ============================================================
-- Migration: sistema_id e subsistema_id em saf_solicitacoes
--
-- Objetivo:
-- 1) Persistir sistema/subsistema selecionados no cadastro da SAF
-- 2) Permitir regra de duplicidade CCM por sistema+subsistema+equipamento
-- ============================================================

ALTER TABLE public.saf_solicitacoes
  ADD COLUMN IF NOT EXISTS sistema_id bigint,
  ADD COLUMN IF NOT EXISTS subsistema_id bigint;

CREATE INDEX IF NOT EXISTS idx_saf_solicitacoes_sistema_subsistema
  ON public.saf_solicitacoes (sistema_id, subsistema_id);

CREATE INDEX IF NOT EXISTS idx_saf_solicitacoes_dup_criteria
  ON public.saf_solicitacoes (sistema_id, subsistema_id, equipamento);
