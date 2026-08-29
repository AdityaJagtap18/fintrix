param baseName string
param environmentName string
param location string
param tags object

@description('Principal ID of the user-assigned identity to grant Service Bus data access to.')
param principalId string

var queueNames = ['invoice-extract', 'invoice-review', 'erp-post']

// T-005: dead-lettering on every queue backs AC-11.3 (exhausted retries dead-letter and
// alert, never retry forever). `requiresSession: true` gives per-vendor ordering (README
// §2: "at-least-once, DLQ, ordered per-vendor sessions") — the application sets the
// vendor ID as the session ID on send.
resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: 'sb-${baseName}-${environmentName}'
  location: location
  tags: tags
  sku: environmentName == 'prd'
    ? {
        name: 'Premium'
        tier: 'Premium'
        capacity: 1
      }
    : {
        name: 'Standard'
        tier: 'Standard'
      }
  properties: {
    disableLocalAuth: true // Managed Identity only — no SAS connection strings (Article III)
    publicNetworkAccess: environmentName == 'prd' ? 'Disabled' : 'Enabled'
  }
}

resource queues 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = [
  for name in queueNames: {
    parent: serviceBusNamespace
    name: name
    properties: {
      deadLetteringOnMessageExpiration: true
      maxDeliveryCount: 10
      lockDuration: 'PT5M'
      defaultMessageTimeToLive: 'P14D'
      requiresSession: true
    }
  }
]

var serviceBusDataOwnerRoleId = '090c5cfd-751d-490a-894a-3ce6f1109419'

resource serviceBusDataOwnerRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, principalId, 'AzureServiceBusDataOwner')
  scope: serviceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serviceBusDataOwnerRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output namespaceName string = serviceBusNamespace.name
output namespaceHostName string = '${serviceBusNamespace.name}.servicebus.windows.net'
