param baseName string
param environmentName string
param location string
param tags object
param identityId string

@description('Client ID of the user-assigned identity — DefaultAzureCredential and the identity-based storage connection both need this explicitly since the Function App has more than one identity available to it otherwise (system + user-assigned).')
param identityClientId string

@description('Name of the storage account this Function App uses for both AzureWebJobsStorage and its own blob/queue triggers.')
param storageAccountName string

param appInsightsConnectionString string

// Elastic Premium: no cold start on the orchestrator (Durable Functions needs to stay
// warm to be checkpointed reliably) but still scales to near-zero between invoice bursts.
resource functionsPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'plan-func-${baseName}-${environmentName}'
  location: location
  tags: tags
  sku: {
    name: 'EP1'
    tier: 'ElasticPremium'
  }
  kind: 'elastic'
  properties: {
    reserved: true
    maximumElasticWorkerCount: environmentName == 'prd' ? 20 : 5
  }
}

// AzureWebJobsStorage uses an identity-based connection (`__accountName` +
// `__credential: managedidentity`), not a connection string — no storage key is ever
// issued to this app (Article III). Requires the Blob/Queue/Table Data Contributor role
// assignments granted in modules/storage.bicep.
resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: 'func-${baseName}-${environmentName}'
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    serverFarmId: functionsPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'AzureWebJobsStorage__accountName', value: storageAccountName }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: identityClientId }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
        { name: 'AZURE_CLIENT_ID', value: identityClientId }
      ]
    }
  }
}

output name string = functionApp.name
output defaultHostName string = functionApp.properties.defaultHostName
