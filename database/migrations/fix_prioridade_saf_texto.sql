-- ============================================================
-- Migration: normaliza saf_solicitacoes.prioridade para texto
--
-- Objetivo:
--   Compatibilizar com a API Flask que envia prioridade como:
--   BAIXA | MEDIA | ALTA | CRITICA
--
-- Script idempotente e seguro para schema legado.
-- ============================================================

DO $$
DECLARE
  v_type text;
BEGIN
  SELECT c.udt_name
    INTO v_type
    FROM information_schema.columns c
   WHERE c.table_schema = 'public'
     AND c.table_name = 'saf_solicitacoes'
     AND c.column_name = 'prioridade'
   LIMIT 1;

  IF v_type IS NULL THEN
    RAISE NOTICE 'Coluna prioridade não encontrada em saf_solicitacoes. Nada a fazer.';
    RETURN;
  END IF;

  -- int2/int4/int8 -> texto com mapeamento
  IF v_type IN ('int2', 'int4', 'int8') THEN
    -- Remove constraints que referenciam prioridade como inteiro ANTES de alterar o tipo
    ALTER TABLE public.saf_solicitacoes
      DROP CONSTRAINT IF EXISTS saf_solicitacoes_prioridade_check;

    ALTER TABLE public.saf_solicitacoes
      ALTER COLUMN prioridade DROP DEFAULT;

    ALTER TABLE public.saf_solicitacoes
      ALTER COLUMN prioridade TYPE text
      USING (
        CASE prioridade
          WHEN 1 THEN 'BAIXA'
          WHEN 2 THEN 'MEDIA'
          WHEN 3 THEN 'ALTA'
          WHEN 4 THEN 'CRITICA'
          ELSE 'MEDIA'
        END
      );
  END IF;

  ALTER TABLE public.saf_solicitacoes
    ALTER COLUMN prioridade SET DEFAULT 'MEDIA';

  BEGIN
    ALTER TABLE public.saf_solicitacoes
      ADD CONSTRAINT saf_solicitacoes_prioridade_check
      CHECK (prioridade IN ('BAIXA', 'MEDIA', 'ALTA', 'CRITICA'));
  EXCEPTION WHEN duplicate_object THEN NULL;
  END;
END $$;
