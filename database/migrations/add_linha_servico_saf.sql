-- Adiciona o campo opcional de linha de servico para SAFs do fluxo MRO.

ALTER TABLE public.saf_solicitacoes
  ADD COLUMN IF NOT EXISTS linha_servico text;

COMMENT ON COLUMN public.saf_solicitacoes.linha_servico
  IS 'Linha de servico selecionada no fluxo MRO (11, 12 ou 13).';
