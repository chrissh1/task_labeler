-- psql only. Supply deployment_environment and database_id with -v.
-- Use a fresh, dedicated Supabase project. Re-running refuses to overwrite it.
\set ON_ERROR_STOP on
BEGIN;
\ir ../study/postgres.sql
INSERT INTO study.deployment_metadata VALUES (1, :'deployment_environment', :'database_id', 1);
CREATE ROLE study_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
\ir grant_runtime.sql
COMMIT;
-- Set the runtime role password securely with psql's interactive \password command.
-- No password is embedded in this script or in shell arguments.
