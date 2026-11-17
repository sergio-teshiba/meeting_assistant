-- ============================================================================
-- Copiloto de Reuniones · Migración de Supabase (idempotente: se puede correr
-- varias veces). Pégalo en Supabase → SQL Editor → Run.
-- ============================================================================

-- 1) Usuarios: columnas que usa la app (no borra nada de lo existente)
create table if not exists public.users (
  id            bigint generated always as identity primary key,
  username      text unique not null,
  password_hash text not null,
  api_key       text default ''
);
alter table public.users add column if not exists is_admin   boolean     not null default false;
alter table public.users add column if not exists plan       text        not null default 'free';
alter table public.users add column if not exists created_at timestamptz not null default now();
alter table public.users add column if not exists plan_source text not null default '';   -- manual | stripe
alter table public.users add column if not exists stripe_customer_id text not null default '';

-- Ajustes de la app (se editan desde la pestaña «Administración»)
create table if not exists public.app_settings (
  key        text primary key,
  value      jsonb,
  updated_at timestamptz not null default now()
);

-- 2) Historial de reuniones (transcripción, tarjetas y minuta en JSON)
create table if not exists public.meetings (
  id         uuid primary key,
  username   text not null references public.users(username) on delete cascade,
  title      text not null default '',
  data       jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists meetings_username_created_idx
  on public.meetings (username, created_at desc);

-- 3) SEGURIDAD (crítico)
-- La versión anterior desactivaba RLS y usaba la llave *publishable*, que es
-- pública por diseño: con ella cualquiera podía leer todos los hashes de
-- contraseña y las API keys de Gemini. A partir de ahora:
--   * RLS activado y SIN políticas → la llave publishable/anon no ve nada.
--   * La app se conecta con la llave SECRETA (sb_secret_…), que solo vive en
--     los secretos del servidor de Streamlit y nunca llega al navegador.
alter table public.users        enable row level security;
alter table public.meetings     enable row level security;
alter table public.app_settings enable row level security;
revoke all on public.users        from anon, authenticated;
revoke all on public.meetings     from anon, authenticated;
revoke all on public.app_settings from anon, authenticated;

-- 4) Planes: ya no hace falta SQL. Cámbialos en la app → pestaña «Administración».

-- Nota: ya NO es necesario crear usuarios con SQL ni con digest()/SHA-256.
-- Regístrate desde la app; para hacerte admin:
-- update public.users set is_admin = true where username = 'tu-correo@dominio.com';
