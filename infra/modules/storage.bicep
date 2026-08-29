param baseName string
param environmentName string
param location string
param tags object

@description('Principal ID of the user-assigned identity to grant data-plane access to.')
param principalId string

var storageAccountName = take(toLower('st${baseName}${environmentName}${uniqueString(resourceGroup().id)}'), 24)
var containerNames = ['raw', 'analyzed', 'quarantine', 'feedback', 'training']

// T-003: containers for the five stages a document passes through, plus the training
// feedback loop. `allowSharedKeyAccess: false` is the done-when condition — no key-based
// access exists at all, only Managed Identity (Article III).
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: environmentName == 'prd' ? 'Standard_GRS' : 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    // Private endpoints replace this in prod (Phase 6, T-102) — dev stays open so a
    // laptop can reach it without a VPN.
    publicNetworkAccess: environmentName == 'prd' ? 'Disabled' : 'Enabled'
    accessTier: 'Hot'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [
  for name in containerNames: {
    parent: blobService
    name: name
    properties: {
      publicAccess: 'None'
    }
  }
]

// README §2 / NFR-031: hot -> cool lifecycle on the two high-volume containers. This is a
// starting default, not the full per-document-class retention/purge policy (NFR-031),
// which is enforced at the application layer (T-089) since it varies by document type.
resource lifecyclePolicy 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'raw-and-analyzed-hot-to-cool'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['raw/', 'analyzed/']
            }
            actions: {
              baseBlob: {
                tierToCool: {
                  daysAfterModificationGreaterThan: 30
                }
              }
            }
          }
        }
      ]
    }
  }
}

// Role definition IDs are fixed GUIDs, the same across every Azure tenant.
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'

// Blob: the app's own raw/analyzed/quarantine/feedback/training containers.
resource blobDataContributorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, principalId, 'StorageBlobDataContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// Queue + Table: this storage account doubles as the Functions runtime's
// AzureWebJobsStorage (identity-based connection, no connection string — see
// modules/functions.bicep), which needs these in addition to blob.
resource queueDataContributorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, principalId, 'StorageQueueDataContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageQueueDataContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource tableDataContributorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, principalId, 'StorageTableDataContributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output name string = storageAccount.name
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
