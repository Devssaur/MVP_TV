-- ============================================================
-- Migration: adiciona coluna sintoma_id em saf_solicitacoes
--
-- Objetivo:
--   Alinhar o schema com a API de criacao de SAF, que envia sintoma_id.
--
-- Comportamento:
--   - Se sintomas_catalogo.id for uuid  -> cria sintoma_id uuid
--   - Se sintomas_catalogo.id for bigint -> cria sintoma_id bigint
--   - Caso não consiga detectar, cria sintoma_id text
--   - Tenta criar FK quando possivel
--
-- Script idempotente: seguro para reexecucao.
-- ============================================================

DO $$
DECLARE
  v_id_type text;
BEGIN
  IF EXISTS (
    SELECT 1
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = 'saf_solicitacoes'
       AND column_name = 'sintoma_id'
  ) THEN
    RAISE NOTICE 'Coluna public.saf_solicitacoes.sintoma_id ja existe. Nada a fazer.';
    RETURN;
  END IF;

  SELECT c.udt_name
    INTO v_id_type
    FROM information_schema.columns c
   WHERE c.table_schema = 'public'
     AND c.table_name = 'sintomas_catalogo'
     AND c.column_name = 'id'
   LIMIT 1;

  IF v_id_type = 'uuid' THEN
    EXECUTE 'ALTER TABLE public.saf_solicitacoes ADD COLUMN sintoma_id uuid';
    BEGIN
      EXECUTE 'ALTER TABLE public.saf_solicitacoes
                 ADD CONSTRAINT saf_solicitacoes_sintoma_id_fkey
                 FOREIGN KEY (sintoma_id)
                 REFERENCES public.sintomas_catalogo(id)
                 ON UPDATE CASCADE ON DELETE SET NULL';
    EXCEPTION WHEN duplicate_object OR undefined_table OR undefined_column THEN
      NULL;
    END;

  ELSIF v_id_type = 'int8' THEN
    EXECUTE 'ALTER TABLE public.saf_solicitacoes ADD COLUMN sintoma_id bigint';
    BEGIN
      EXECUTE 'ALTER TABLE public.saf_solicitacoes
                 ADD CONSTRAINT saf_solicitacoes_sintoma_id_fkey
                 FOREIGN KEY (sintoma_id)
                 REFERENCES public.sintomas_catalogo(id)
                 ON UPDATE CASCADE ON DELETE SET NULL';
    EXCEPTION WHEN duplicate_object OR undefined_table OR undefined_column THEN
      NULL;
    END;

  ELSE
    EXECUTE 'ALTER TABLE public.saf_solicitacoes ADD COLUMN sintoma_id text';
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_saf_solicitacoes_sintoma_id
  ON public.saf_solicitacoes(sintoma_id);
