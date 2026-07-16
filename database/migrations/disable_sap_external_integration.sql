-- Migração: desativar integração externa com SAP
-- Objetivo:
--   1) Desativar job automático de sync externo (pg_cron / edge function).
--   2) Manter estrutura de dados de qmnum no banco para preenchimento manual.

begin;

-- 1) Remove agendamento legado da sync externa, se existir
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM cron.job
    WHERE jobname = 'sync-mestres-sap'
  ) THEN
    PERFORM cron.unschedule('sync-mestres-sap');
  END IF;
EXCEPTION
  WHEN undefined_table THEN
    -- Extensão pg_cron não instalada neste ambiente.
    NULL;
END;
$$;

-- 2) Garante defaults seguros para fluxo manual
ALTER TABLE public.saf_integracao_sap
  ALTER COLUMN status_integracao SET DEFAULT 'PENDENTE';

COMMENT ON TABLE public.saf_integracao_sap
  IS 'Registro de integração SAP. Integração externa desativada; qmnum é registrado manualmente.';

COMMENT ON COLUMN public.saf_integracao_sap.qmnum
  IS 'Número SAP (QMNUM) informado manualmente pelo processo operacional.';

commit;
