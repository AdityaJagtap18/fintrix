param baseName string
param environmentName string
param location string
param tags object

@description('Principal ID of the user-assigned identity to grant Cognitive Services access to.')
param principalId string

// T-004: `disableLocalAuth: true` means a key-based call is rejected outright (401) —
// Managed Identity via Entra token is the only way in (Article III).
resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'di-${baseName}-${environmentName}'
  location: location
  tags: tags
  kind: 'FormRecognizer'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: 'di-${baseName}-${environmentName}'
    disableLocalAuth: true
    publicNetworkAccess: environmentName == 'prd' ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource cognitiveServicesUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(documentIntelligence.id, principalId, 'CognitiveServicesUser')
  scope: documentIntelligence
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output endpoint string = documentIntelligence.properties.endpoint
output name string = documentIntelligence.name
