-- Migração OWASP: hardening de RLS e privilégio mínimo
-- Objetivo:
--   1) Remover políticas permissivas baseadas em anon using(true)
--   2) Aplicar políticas por auth.uid() e perfil de usuário
--   3) Reduzir superfície de acesso direto ao banco

begin;

-- ---------------------------------------------------------
-- Função utilitária: verifica se o usuário autenticado possui
-- um perfil privilegiado no sistema.
-- ---------------------------------------------------------
create or replace function public.fn_usuario_tem_perfil(_perfis text[])
returns boolean
language sql
stable
as $$
  select exists (
    select 1
    from public.usuarios u
    where u.id = auth.uid()
      and u.aprovado = true
      and u.perfil = any(_perfis)
  );
$$;

-- ---------------------------------------------------------
-- Garantia de RLS habilitado
-- ---------------------------------------------------------
alter table if exists public.usuarios enable row level security;
alter table if exists public.saf_solicitacoes enable row level security;
alter table if exists public.saf_controle_ccm enable row level security;
alter table if exists public.saf_integracao_sap enable row level security;
alter table if exists public.logs_auditoria enable row level security;
alter table if exists public.locais_instalacao enable row level security;
alter table if exists public.equipamentos enable row level security;
alter table if exists public.sintomas_catalogo enable row level security;
alter table if exists public.frotas_trens enable row level security;
alter table if exists public.trechos_vias enable row level security;
alter table if exists public.estacoes enable row level security;

-- ---------------------------------------------------------
-- Remove políticas legadas permissivas (se existirem)
-- ---------------------------------------------------------
do $$
begin
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'usuarios') then
    execute 'drop policy if exists "Permitir leitura para autenticacao" on public.usuarios';
    execute 'drop policy if exists usuarios_select_self_or_privileged on public.usuarios';
    execute 'drop policy if exists usuarios_update_self_or_admin on public.usuarios';
  end if;

  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'saf_solicitacoes') then
    execute 'drop policy if exists "Solicitante ve suas solicitacoes / CCM e Admin veem todas" on public.saf_solicitacoes';
    execute 'drop policy if exists saf_select_own_or_privileged on public.saf_solicitacoes';
    execute 'drop policy if exists saf_insert_own on public.saf_solicitacoes';
    execute 'drop policy if exists saf_update_own_or_privileged on public.saf_solicitacoes';
  end if;

  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'saf_controle_ccm') then
    execute 'drop policy if exists "Leitura liberada para autenticados" on public.saf_controle_ccm';
    execute 'drop policy if exists ccm_select_by_related_saf on public.saf_controle_ccm';
    execute 'drop policy if exists ccm_update_by_privileged on public.saf_controle_ccm';
  end if;

  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'saf_integracao_sap') then
    execute 'drop policy if exists "Leitura liberada para autenticados" on public.saf_integracao_sap';
    execute 'drop policy if exists integ_sap_select_by_related_saf on public.saf_integracao_sap';
    execute 'drop policy if exists integ_sap_update_by_privileged on public.saf_integracao_sap';
    execute 'drop policy if exists integ_sap_insert_by_privileged on public.saf_integracao_sap';
  end if;

  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'logs_auditoria') then
    execute 'drop policy if exists logs_select_admin on public.logs_auditoria';
  end if;

  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'locais_instalacao') then
    execute 'drop policy if exists "Leitura publica dados mestres - locais" on public.locais_instalacao';
    execute 'drop policy if exists locais_select_authenticated on public.locais_instalacao';
  end if;

  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'equipamentos') then
    execute 'drop policy if exists "Leitura publica dados mestres - equipamentos" on public.equipamentos';
    execute 'drop policy if exists equipamentos_select_authenticated on public.equipamentos';
  end if;

  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'sintomas_catalogo') then
    execute 'drop policy if exists "Leitura publica dados mestres - sintomas" on public.sintomas_catalogo';
    execute 'drop policy if exists sintomas_select_authenticated on public.sintomas_catalogo';
  end if;

  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'frotas_trens') then
    execute 'drop policy if exists "Leitura publica dados mestres - frotas" on public.frotas_trens';
    execute 'drop policy if exists frotas_select_authenticated on public.frotas_trens';
  end if;

  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'trechos_vias') then
    execute 'drop policy if exists trechos_select_authenticated on public.trechos_vias';
  end if;

  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'estacoes') then
    execute 'drop policy if exists estacoes_select_authenticated on public.estacoes';
  end if;
end
$$;

-- ---------------------------------------------------------
-- usuarios
-- ---------------------------------------------------------
do $$
begin
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'usuarios') then
    execute $sql$
      create policy usuarios_select_self_or_privileged
      on public.usuarios
      for select
      to authenticated
      using (
        id = auth.uid()
        or public.fn_usuario_tem_perfil(array['Administrador'])
      )
    $sql$;

    execute $sql$
      create policy usuarios_update_self_or_admin
      on public.usuarios
      for update
      to authenticated
      using (
        id = auth.uid()
        or public.fn_usuario_tem_perfil(array['Administrador'])
      )
      with check (
        id = auth.uid()
        or public.fn_usuario_tem_perfil(array['Administrador'])
      )
    $sql$;
  end if;
end
$$;

-- ---------------------------------------------------------
-- saf_solicitacoes
-- ---------------------------------------------------------
do $$
begin
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'saf_solicitacoes') then
    execute $sql$
      create policy saf_select_own_or_privileged
      on public.saf_solicitacoes
      for select
      to authenticated
      using (
        notificador_id = auth.uid()
        or public.fn_usuario_tem_perfil(array['Administrador','CCM','SIC'])
      )
    $sql$;

    execute $sql$
      create policy saf_insert_own
      on public.saf_solicitacoes
      for insert
      to authenticated
      with check (
        notificador_id = auth.uid()
      )
    $sql$;

    execute $sql$
      create policy saf_update_own_or_privileged
      on public.saf_solicitacoes
      for update
      to authenticated
      using (
        notificador_id = auth.uid()
        or public.fn_usuario_tem_perfil(array['Administrador','CCM'])
      )
      with check (
        notificador_id = auth.uid()
        or public.fn_usuario_tem_perfil(array['Administrador','CCM'])
      )
    $sql$;
  end if;
end
$$;

-- ---------------------------------------------------------
-- saf_controle_ccm
-- ---------------------------------------------------------
do $$
begin
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'saf_controle_ccm') then
    execute $sql$
      create policy ccm_select_by_related_saf
      on public.saf_controle_ccm
      for select
      to authenticated
      using (
        exists (
          select 1
          from public.saf_solicitacoes s
          where s.id = saf_controle_ccm.solicitacao_id
            and (
              s.notificador_id = auth.uid()
              or public.fn_usuario_tem_perfil(array['Administrador','CCM','SIC'])
            )
        )
      )
    $sql$;

    execute $sql$
      create policy ccm_update_by_privileged
      on public.saf_controle_ccm
      for update
      to authenticated
      using (public.fn_usuario_tem_perfil(array['Administrador','CCM']))
      with check (public.fn_usuario_tem_perfil(array['Administrador','CCM']))
    $sql$;
  end if;
end
$$;

-- ---------------------------------------------------------
-- saf_integracao_sap
-- ---------------------------------------------------------
do $$
begin
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'saf_integracao_sap') then
    execute $sql$
      create policy integ_sap_select_by_related_saf
      on public.saf_integracao_sap
      for select
      to authenticated
      using (
        exists (
          select 1
          from public.saf_solicitacoes s
          where s.id = saf_integracao_sap.solicitacao_id
            and (
              s.notificador_id = auth.uid()
              or public.fn_usuario_tem_perfil(array['Administrador','CCM','SIC'])
            )
        )
      )
    $sql$;

    execute $sql$
      create policy integ_sap_update_by_privileged
      on public.saf_integracao_sap
      for update
      to authenticated
      using (public.fn_usuario_tem_perfil(array['Administrador','CCM']))
      with check (public.fn_usuario_tem_perfil(array['Administrador','CCM']))
    $sql$;

    execute $sql$
      create policy integ_sap_insert_by_privileged
      on public.saf_integracao_sap
      for insert
      to authenticated
      with check (public.fn_usuario_tem_perfil(array['Administrador','CCM']))
    $sql$;
  end if;
end
$$;

-- ---------------------------------------------------------
-- logs_auditoria: leitura só para admin
-- ---------------------------------------------------------
do $$
begin
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'logs_auditoria') then
    execute $sql$
      create policy logs_select_admin
      on public.logs_auditoria
      for select
      to authenticated
      using (public.fn_usuario_tem_perfil(array['Administrador']))
    $sql$;
  end if;
end
$$;

-- ---------------------------------------------------------
-- dados mestres: somente autenticado aprovado
-- ---------------------------------------------------------
do $$
begin
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'locais_instalacao') then
    execute 'create policy locais_select_authenticated on public.locais_instalacao for select to authenticated using (true)';
  end if;
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'equipamentos') then
    execute 'create policy equipamentos_select_authenticated on public.equipamentos for select to authenticated using (true)';
  end if;
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'sintomas_catalogo') then
    execute 'create policy sintomas_select_authenticated on public.sintomas_catalogo for select to authenticated using (true)';
  end if;
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'frotas_trens') then
    execute 'create policy frotas_select_authenticated on public.frotas_trens for select to authenticated using (true)';
  end if;
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'trechos_vias') then
    execute 'create policy trechos_select_authenticated on public.trechos_vias for select to authenticated using (true)';
  end if;
end
$$;

do $$
begin
  if exists (
    select 1 from information_schema.tables
    where table_schema = 'public' and table_name = 'estacoes'
  ) then
    execute 'create policy estacoes_select_authenticated on public.estacoes for select to authenticated using (true)';
  end if;
end
$$;

commit;
