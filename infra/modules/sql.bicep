param baseName string
param environmentName string
param location string
param tags object

@description('Display name of the Entra user or group that administers the SQL server.')
param sqlAdminLoginName string

@description('Object ID of that Entra user or group.')
param sqlAdminLoginObjectId string

@description('Resource ID of the user-assigned identity, attached to the server for future CMK/backup scenarios.')
param identityId string

// T-006: `azureADOnlyAuthentication: true` means there is no SQL-auth login, ever — Entra
// is the only identity provider (Article III, plan.md "Azure SQL with Entra-only
// authentication"). The application identity itself is granted access at the database
// level, not here — see infra/sql/migrations/0002_audit_log_grants.sql, which also runs
// `CREATE USER ... FROM EXTERNAL PROVIDER` for the app's user-assigned identity.
resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: 'sql-${baseName}-${environmentName}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    administrators: {
      administratorType: 'ActiveDirectory'
      login: sqlAdminLoginName
      sid: sqlAdminLoginObjectId
      tenantId: subscription().tenantId
      azureADOnlyAuthentication: true
    }
    minimalTlsVersion: '1.2'
    publicNetworkAccess: environmentName == 'prd' ? 'Disabled' : 'Enabled'
  }
}

// Serverless, auto-pausing in dev to keep idle cost near zero; always-on in prod.
resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: 'sqldb-${baseName}'
  location: location
  tags: tags
  sku: {
    name: 'GP_S_Gen5'
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: 2
  }
  properties: {
    autoPauseDelay: environmentName == 'prd' ? -1 : 60
    minCapacity: json('0.5')
    zoneRedundant: false
  }
}

// The well-known "allow Azure services" rule. Replaced by a private endpoint in prod
// (Phase 6, T-102) — dev keeps it so Functions/Container Apps on public networking can
// reach the server without a VNet.
resource allowAzureServices 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = if (environmentName != 'prd') {
  parent: sqlServer
  name: 'AllowAllWindowsAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output serverName string = sqlServer.name
output serverFqdn string = sqlServer.properties.fullyQualifiedDomainName
output databaseName string = sqlDatabase.name
