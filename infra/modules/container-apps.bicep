param baseName string
param environmentName string
param location string
param tags object
param logAnalyticsWorkspaceId string
param logAnalyticsCustomerId string

// T-007: the shared Container Apps environment. The review API itself (FastAPI, T-060+)
// deploys into this in Phase 3 — this module only provisions the environment, since there
// is no container image to run yet.
resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${baseName}-${environmentName}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: listKeys(logAnalyticsWorkspaceId, '2023-09-01').primarySharedKey
      }
    }
    zoneRedundant: environmentName == 'prd'
  }
}

output id string = containerAppsEnv.id
output name string = containerAppsEnv.name
output defaultDomain string = containerAppsEnv.properties.defaultDomain
