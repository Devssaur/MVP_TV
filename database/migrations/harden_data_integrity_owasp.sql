-- Migração OWASP: integridade de dados e consistência
-- Objetivo:
--   1) Validar formato do QMNUM informado manualmente
--   2) Garantir enum de status de integração alinhado ao backend
--   3) Evitar payloads de erro excessivos no banco

begin;

-- ---------------------------------------------------------
-- Normaliza possíveis valores inválidos antes das constraints
-- ---------------------------------------------------------
update public.saf_integracao_sap
set status_integracao = 'PENDENTE'
where status_integracao not in ('PENDENTE', 'SUCESSO', 'ERRO', 'CANCELADO');

-- ---------------------------------------------------------
-- Constraint de domínio para status de integração
-- ---------------------------------------------------------
alter table public.saf_integracao_sap
  drop constraint if exists saf_integracao_sap_status_integracao_check;

alter table public.saf_integracao_sap
  add constraint saf_integracao_sap_status_integracao_check
  check (status_integracao in ('PENDENTE', 'SUCESSO', 'ERRO', 'CANCELADO'));

-- ---------------------------------------------------------
-- Constraint de formato QMNUM
-- Aceita nulo ou 6 a 20 dígitos numéricos.
-- ---------------------------------------------------------
alter table public.saf_integracao_sap
  drop constraint if exists saf_integracao_sap_qmnum_format_check;

alter table public.saf_integracao_sap
  add constraint saf_integracao_sap_qmnum_format_check
  check (qmnum is null or qmnum ~ '^[0-9]{6,20}$');

-- ---------------------------------------------------------
-- Índice parcial para consultas operacionais de pendência SAP
-- ---------------------------------------------------------
create index if not exists idx_saf_integracao_sap_pendentes
  on public.saf_integracao_sap (status_integracao, ultima_tentativa_em desc)
  where status_integracao in ('PENDENTE', 'ERRO');

commit;
