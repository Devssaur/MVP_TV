-- ============================================================
-- Migration: adiciona campos de VIA em saf_solicitacoes
--
-- Objetivo:
--   Persistir dados operacionais do fluxo VIA no cadastro da SAF:
--     - via_numero (apenas digitos)
--     - km_inicial (formato xx/xx)
--     - km_final   (formato xx/xx)
--
-- Idempotente: pode ser executada mais de uma vez.
-- ============================================================

-- 1) Colunas
ALTER TABLE public.saf_solicitacoes
  ADD COLUMN IF NOT EXISTS via_numero varchar(3),
  ADD COLUMN IF NOT EXISTS km_inicial varchar(5),
  ADD COLUMN IF NOT EXISTS km_final varchar(5);

-- 2) Constraints de formato (somente se ainda nao existirem)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chk_saf_via_numero_digits'
      AND conrelid = 'public.saf_solicitacoes'::regclass
  ) THEN
    ALTER TABLE public.saf_solicitacoes
      ADD CONSTRAINT chk_saf_via_numero_digits
      CHECK (via_numero IS NULL OR via_numero ~ '^[0-9]{1,3}$');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chk_saf_km_inicial_format'
      AND conrelid = 'public.saf_solicitacoes'::regclass
  ) THEN
    ALTER TABLE public.saf_solicitacoes
      ADD CONSTRAINT chk_saf_km_inicial_format
      CHECK (km_inicial IS NULL OR km_inicial ~ '^[0-9]{2}/[0-9]{2}$');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chk_saf_km_final_format'
      AND conrelid = 'public.saf_solicitacoes'::regclass
  ) THEN
    ALTER TABLE public.saf_solicitacoes
      ADD CONSTRAINT chk_saf_km_final_format
      CHECK (km_final IS NULL OR km_final ~ '^[0-9]{2}/[0-9]{2}$');
  END IF;
END $$;

-- 3) Indice opcional para busca operacional por VIA
CREATE INDEX IF NOT EXISTS idx_saf_solicitacoes_via_numero
  ON public.saf_solicitacoes(via_numero);
