-- 在完整 Compose 初始化之后执行；任一断言失败就让 CI 失败。
BEGIN;
DO $$
BEGIN
    IF (SELECT count(*) FROM pg_roles WHERE rolname IN ('writing_owner', 'writing_app')) <> 2 THEN
        RAISE EXCEPTION '缺少迁移账户或应用账户';
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname IN ('writing_owner', 'writing_app')
               AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR NOT rolcanlogin)) THEN
        RAISE EXCEPTION '业务账户拥有管理权限或无法登录';
    END IF;
    IF (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()) <> 'writing_owner'
       OR (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public') <> 'writing_owner' THEN
        RAISE EXCEPTION '数据库或 public 模式的所有者不正确';
    END IF;
    IF pg_has_role('writing_app', 'writing_owner', 'MEMBER')
       OR has_schema_privilege('writing_app', 'public', 'CREATE') THEN
        RAISE EXCEPTION '应用账户不应继承迁移权限或创建表';
    END IF;
END $$;
SET ROLE writing_owner;
CREATE TABLE public._deployment_access_probe (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, content text);
RESET ROLE;
DO $$
DECLARE permission text;
BEGIN
    FOREACH permission IN ARRAY ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE'] LOOP
        IF NOT has_table_privilege('writing_app', 'public._deployment_access_probe', permission) THEN
            RAISE EXCEPTION '新表没有给应用账户授权 %', permission;
        END IF;
    END LOOP;
    IF NOT has_sequence_privilege('writing_app', 'public._deployment_access_probe_id_seq', 'USAGE') THEN
        RAISE EXCEPTION '新序列没有给应用账户授权';
    END IF;
END $$;
SET ROLE writing_app;
INSERT INTO public._deployment_access_probe(content) VALUES ('权限验收');
UPDATE public._deployment_access_probe SET content = '验收完成';
SELECT count(*) FROM public._deployment_access_probe;
DELETE FROM public._deployment_access_probe;
RESET ROLE;
ROLLBACK;
