@description('Base name used to derive resource names.')
param baseName string

@description('Environment name: dev or prd.')
param environmentName string

param location string
param tags object

// One user-assigned identity, attached to every compute resource (Functions, Container
// Apps) and granted RBAC on every data resource it needs to touch. Managed Identity only —
// constitution Article III: no keys, no connection strings, no secrets in config.
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${baseName}-${environmentName}'
  location: location
  tags: tags
}

output id string = uami.id
output principalId string = uami.properties.principalId
output clientId string = uami.properties.clientId
output name string = uami.name
