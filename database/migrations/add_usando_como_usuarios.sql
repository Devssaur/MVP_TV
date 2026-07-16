-- Migração: adiciona coluna de modo de uso no cadastro de usuários
-- Objetivo:
--   1) preservar o perfil real do usuário
--   2) permitir que o usuário use o sistema em outro modo (ex.: CCM, SIC, Solicitante)
--   3) manter compatibilidade com a lógica atual do front-end

begin;

alter table public.usuarios
  add column if not exists usando_como text;

update public.usuarios
set usando_como = perfil
where usando_como is null;

alter table public.usuarios
  alter column usando_como set default null;

alter table public.usuarios
  drop constraint if exists usuarios_usando_como_check;

alter table public.usuarios
  add constraint usuarios_usando_como_check
  check (usando_como is null or usando_como in ('SOLICITANTE', 'CCM', 'ADMIN', 'SIC'));

create index if not exists idx_usuarios_usando_como
  on public.usuarios (usando_como);

commit;
