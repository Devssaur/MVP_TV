-- Adiciona suporte ao perfil Manutenção na tabela de usuários.
-- Inclui:
--  1) perfil = 'Manutenção'
--  2) usando_como = 'MANUTENCAO'

begin;

alter table if exists public.usuarios
  drop constraint if exists usuarios_perfil_check;

alter table if exists public.usuarios
  add constraint usuarios_perfil_check
  check (perfil in ('Solicitante', 'CCM', 'Administrador', 'SIC', 'Manutenção'));

alter table if exists public.usuarios
  drop constraint if exists usuarios_usando_como_check;

alter table if exists public.usuarios
  add constraint usuarios_usando_como_check
  check (usando_como is null or usando_como in ('SOLICITANTE', 'CCM', 'ADMIN', 'SIC', 'MANUTENCAO'));

commit;
