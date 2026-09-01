DO $verify_data$
DECLARE
    inconsistent_admins BIGINT;
BEGIN
    IF (SELECT COUNT(DISTINCT config_key) FROM public.system_configs
         WHERE config_key IN ('brand_name', 'app_title') AND is_active) <> 2 THEN
        RAISE EXCEPTION 'required active system configuration is missing';
    END IF;
    SELECT COUNT(*) INTO inconsistent_admins FROM public.users
     WHERE is_admin IS DISTINCT FROM (role IN ('admin', 'super_admin'));
    IF inconsistent_admins > 0 THEN
        RAISE EXCEPTION 'users contain % inconsistent role/is_admin rows', inconsistent_admins;
    END IF;
END
$verify_data$;

SELECT json_build_object(
    'users', (SELECT COUNT(*) FROM public.users),
    'workers', (SELECT COUNT(*) FROM public.workers),
    'projects', (SELECT COUNT(*) FROM public.projects),
    'scheduled_tasks', (SELECT COUNT(*) FROM public.scheduled_tasks),
    'task_executions', (SELECT COUNT(*) FROM public.task_executions),
    'system_configs', (SELECT COUNT(*) FROM public.system_configs),
    'audit_logs', (SELECT COUNT(*) FROM public.audit_logs)
) AS restored_critical_row_counts;
