-- Run as the schema owner after provisioning or restoring a backup.
REVOKE ALL ON SCHEMA study FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA study FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA study FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA study FROM PUBLIC;
GRANT USAGE ON SCHEMA study TO study_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA study TO study_runtime;
GRANT INSERT ON study.study_config, study.participants, study.sessions, study.trials,
 study.attention, study.events, study.checkpoints, study.ai_calls TO study_runtime;
GRANT UPDATE ON study.sessions, study.trials, study.checkpoints, study.ai_calls TO study_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA study TO study_runtime;
