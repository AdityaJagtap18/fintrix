using 'main.bicep'

param environmentName = 'dev'
param location = 'centralindia'
param baseName = 'apauto'

// Placeholder — replace before deploying. Find your own Entra object ID with:
//   az ad signed-in-user show --query id -o tsv
// or, for a group that should have Cosmos DB data-plane access instead of an individual:
//   az ad group show --group "<group-name>" --query id -o tsv
param cosmosDataAccessPrincipalId = '00000000-0000-0000-0000-000000000000'
