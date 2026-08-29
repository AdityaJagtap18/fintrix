-- 0002_audit_log_grants.sql
-- constitution Article VIII / T-046 done-when: "an UPDATE against audit_log as the
-- application role is rejected by the database." Enforced here, not only by the app
-- never issuing an UPDATE — a bug or a compromised credential must not be able to alter
-- history either.
--
-- Run this as a principal with ALTER permission on the schema (deployment/db-owner
-- context), not as the application identity itself.
--
-- $(AppIdentityName) is a sqlcmd variable — the deploy pipeline (cd.yml) substitutes the
-- user-assigned managed identity's display name, matching plan.md's "Azure SQL with
-- Entra-only authentication."

:setvar AppIdentityName ap-automation-app-identity

-- Maps the managed identity to a database user (Entra-only auth — no password, no key).
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = '$(AppIdentityName)')
BEGIN
    CREATE USER [$(AppIdentityName)] FROM EXTERNAL PROVIDER;
END
GO

-- Baseline: the app identity can read and write everywhere except where explicitly denied
-- below. db_datawriter/db_datareader, not db_owner — the application never needs schema
-- changes at runtime (those go through this migrations folder instead).
ALTER ROLE db_datareader ADD MEMBER [$(AppIdentityName)];
ALTER ROLE db_datawriter ADD MEMBER [$(AppIdentityName)];
GO

-- audit_log is INSERT/SELECT only. DENY overrides any GRANT the role membership above
-- would otherwise imply, and DENY beats GRANT even if something else later grants UPDATE
-- to this identity by mistake — that asymmetry is the point.
DENY UPDATE ON dbo.audit_log TO [$(AppIdentityName)];
DENY DELETE ON dbo.audit_log TO [$(AppIdentityName)];
GO

GRANT INSERT ON dbo.audit_log TO [$(AppIdentityName)];
GRANT SELECT ON dbo.audit_log TO [$(AppIdentityName)];
GO
