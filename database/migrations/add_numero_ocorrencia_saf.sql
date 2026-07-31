-- Adiciona o campo opcional de numero de ocorrencia na tabela principal de SAFs.
-- Permite pesquisa e exibicao do numero em todas as filas/tabelas da aplicacao.

ALTER TABLE public.saf_solicitacoes
  ADD COLUMN IF NOT EXISTS numero_ocorrencia text;

COMMENT ON COLUMN public.saf_solicitacoes.numero_ocorrencia
  IS 'Numero de ocorrencia informado pelo solicitante (opcional).';
