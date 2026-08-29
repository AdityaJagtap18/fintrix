param baseName string
param environmentName string
param location string
param tags object

// T-006 / plan.md: Log Analytics + App Insights, feeding the OpenTelemetry metrics
// (STP rate, cost/doc, confidence histogram) that the dashboard and admin console read.
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${baseName}-${environmentName}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: environmentName == 'prd' ? 90 : 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${baseName}-${environmentName}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
  }
}

output logAnalyticsWorkspaceId string = logAnalytics.id
output logAnalyticsCustomerId string = logAnalytics.properties.customerId
output appInsightsConnectionString string = appInsights.properties.ConnectionString
