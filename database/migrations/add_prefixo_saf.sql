-- Adiciona o campo opcional de prefixo para SAFs do fluxo MRO.

ALTER TABLE public.saf_solicitacoes
  ADD COLUMN IF NOT EXISTS prefixo text;

COMMENT ON COLUMN public.saf_solicitacoes.prefixo
  IS 'Prefixo informado pelo solicitante no fluxo MRO.';
