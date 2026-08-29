targetScope = 'subscription'

@description('Environment name — governs networking and SKU defaults per plan.md §9 (dev: public networking, consumption/scale-to-zero; prd: private endpoints, Premium, min 2 replicas — private networking lands in Phase 6, T-102).')
@allowed(['dev', 'prd'])
param environmentName string = 'dev'

@description('Azure region for every resource.')
param location string = 'centralindia'

@description('Base name used to derive resource names. Keep short — several resource types (storage accounts especially) have tight length limits once the environment suffix and uniqueness suffix are appended.')
@maxLength(12)
param baseName string = 'apauto'

@description('Display name of the Entra user or group that administers Azure SQL (T-006). Customer-specific — no sensible default.')
param sqlAdminLoginName string

@description('Object ID of that Entra user or group.')
param sqlAdminLoginObjectId string

var resourceGroupName = 'rg-${baseName}-${environmentName}'
var tags = {
  project: 'invoice-iq'
  environment: environmentName
  managedBy: 'bicep'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  scope: rg
  params: {
    baseName: baseName
    environmentName: environmentName
    location: location
    tags: tags
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    baseName: baseName
    environmentName: environmentName
    location: location
    tags: tags
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    baseName: baseName
    environmentName: environmentName
    location: location
    tags: tags
    principalId: identity.outputs.principalId
  }
}

module documentIntelligence 'modules/document-intelligence.bicep' = {
  name: 'documentIntelligence'
  scope: rg
  params: {
    baseName: baseName
    environmentName: environmentName
    location: location
    tags: tags
    principalId: identity.outputs.principalId
  }
}

module serviceBus 'modules/service-bus.bicep' = {
  name: 'serviceBus'
  scope: rg
  params: {
    baseName: baseName
    environmentName: environmentName
    location: location
    tags: tags
    principalId: identity.outputs.principalId
  }
}

module sql 'modules/sql.bicep' = {
  name: 'sql'
  scope: rg
  params: {
    baseName: baseName
    environmentName: environmentName
    location: location
    tags: tags
    identityId: identity.outputs.id
    sqlAdminLoginName: sqlAdminLoginName
    sqlAdminLoginObjectId: sqlAdminLoginObjectId
  }
}

module containerApps 'modules/container-apps.bicep' = {
  name: 'containerApps'
  scope: rg
  params: {
    baseName: baseName
    environmentName: environmentName
    location: location
    tags: tags
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    logAnalyticsCustomerId: monitoring.outputs.logAnalyticsCustomerId
  }
}

module functions 'modules/functions.bicep' = {
  name: 'functions'
  scope: rg
  params: {
    baseName: baseName
    environmentName: environmentName
    location: location
    tags: tags
    identityId: identity.outputs.id
    identityClientId: identity.outputs.clientId
    storageAccountName: storage.outputs.name
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
  }
}

output resourceGroupName string = rg.name
output identityClientId string = identity.outputs.clientId
output documentIntelligenceEndpoint string = documentIntelligence.outputs.endpoint
output storageAccountUrl string = storage.outputs.blobEndpoint
output serviceBusNamespaceHostName string = serviceBus.outputs.namespaceHostName
output sqlServerFqdn string = sql.outputs.serverFqdn
output sqlDatabaseName string = sql.outputs.databaseName
output containerAppsEnvironmentId string = containerApps.outputs.id
output functionAppName string = functions.outputs.name
