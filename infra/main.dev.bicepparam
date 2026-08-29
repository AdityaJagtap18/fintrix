using 'main.bicep'

param environmentName = 'dev'
param location = 'centralindia'
param baseName = 'apauto'

// Placeholders — replace before deploying. Find your own Entra object ID with:
//   az ad signed-in-user show --query id -o tsv
// or, for a group that should administer SQL instead of an individual:
//   az ad group show --group "<group-name>" --query id -o tsv
param sqlAdminLoginName = 'REPLACE_WITH_YOUR_ENTRA_UPN_OR_GROUP_NAME'
param sqlAdminLoginObjectId = '00000000-0000-0000-0000-000000000000'
