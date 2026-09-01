param baseName string
param environmentName string
param location string
param tags object

@description('Object ID of the Entra user or group granted data-plane access for local development. Mirrors the former Azure SQL admin login (sqlAdminLoginObjectId) — full data-plane control, not scoped per-container like the app identity below.')
param cosmosDataAccessPrincipalId string

@description('Resource ID of the user-assigned identity, attached to the account for authentication.')
param identityId string

@description('Principal ID of that same identity, for Cosmos data-plane RBAC role assignments. Cosmos RBAC (Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments) is a separate system from Microsoft.Authorization/roleAssignments used elsewhere in infra/ — it has no built-in scoping to individual containers via the generic ARM role system, so it is handled entirely within this module.')
param principalId string

var databaseName = 'db-${baseName}'

// Containers that get the (scoped, per-container) built-in Data Contributor role. audit_log
// is deliberately excluded — see the restricted role definition below.
var nonAuditContainerNames = [
  'vendors'
  'vendor_tax_ids'
  'documents'
  'invoices'
  'purchase_orders'
  'review_assignments'
  'corrections'
  'model_registry'
]

// T-006 (Cosmos DB variant): disableLocalAuth means there is no primary/secondary key
// auth, ever — Entra RBAC via sqlRoleAssignments below is the only identity provider
// (Article III, plan.md "Cosmos DB with Entra-only authentication"). Serverless capacity
// mode is the cost/ops call behind the plan.md "Transactional store" decision: no
// provisioned RU/s and no idle floor, unlike the Azure SQL GP_S_Gen5 tier this replaces
// (which still had an always-on minCapacity in prod).
resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: 'cosmos-${baseName}-${environmentName}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
    // Private endpoints replace this in prod (Phase 6, T-102) — dev keeps it so Functions/
    // Container Apps on public networking can reach the account without a VNet. Same split
    // as the SQL server firewall rule this replaces.
    publicNetworkAccess: environmentName == 'prd' ? 'Disabled' : 'Enabled'
    capabilities: [
      { name: 'EnableServerless' }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

// ============================================================================
// Containers — mirrors plan.md §3 / the tables formerly in
// infra/sql/migrations/0001_initial_schema.sql. Partition keys and embedding choices are
// explained per-container in plan.md; this module only needs to declare the physical
// shape (partition key, unique key policy where BR-04 needs it).
// ============================================================================

// dbo.vendors. BR-12's active_vendors() candidate pool is a cross-partition
// `WHERE is_active = true` query — fine at vendor-master scale (thousands of rows, not
// per-invoice volume). Fuzzy matching itself already runs in Python (RapidFuzz) against
// this pool, not as a DB-side trigram search, so nothing is lost versus the SQL index.
resource vendorsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'vendors'
  properties: {
    resource: {
      id: 'vendors'
      partitionKey: {
        paths: ['/vendor_id']
        kind: 'Hash'
      }
    }
  }
}

// Lookup-only container reproducing the SQL UNIQUE constraint on vendors.tax_id: an item
// create with id = tax_id fails with 409 Conflict on a duplicate, which the vendor-create
// repository code uses to enforce global tax-id uniqueness before/alongside writing to
// `vendors`. This two-write pattern is the honest cost of dropping a single DB constraint —
// see plan.md's tradeoffs section.
resource vendorTaxIdsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'vendor_tax_ids'
  properties: {
    resource: {
      id: 'vendor_tax_ids'
      partitionKey: {
        paths: ['/tax_id']
        kind: 'Hash'
      }
    }
  }
}

// dbo.documents. Partitioned by content_hash rather than document_id (its SQL PK), with
// item id = content_hash too (document_id lives as a regular property instead) — a
// point-read by id needs the id upfront, and content_hash is the only thing known before
// this lookup runs. Makes FR-003's dedupe-cache lookup — the whole point of Article XII's
// cost gate — a true point read instead of a cross-partition query.
resource documentsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'documents'
  properties: {
    resource: {
      id: 'documents'
      partitionKey: {
        paths: ['/content_hash']
        kind: 'Hash'
      }
    }
  }
}

// dbo.invoices + invoice_fields + invoice_lines + validation_results, the last three
// embedded inline (they're always read/written with the invoice — see plan.md §3).
// Partitioned by vendor_id (sentinel "unresolved" before vendor resolution runs) so BR-04's
// duplicate check reproduces as a unique key policy scoped to that partition — the same
// semantics as the SQL filtered unique index on (vendor_id, invoice_number).
resource invoicesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'invoices'
  properties: {
    resource: {
      id: 'invoices'
      partitionKey: {
        paths: ['/vendor_id']
        kind: 'Hash'
      }
      uniqueKeyPolicy: {
        uniqueKeys: [
          { paths: ['/invoice_number'] }
        ]
      }
    }
  }
}

// dbo.purchase_orders + purchase_order_lines + goods_receipts, the latter two embedded
// inline. This is what makes core.matching.three_way.match_invoice()'s already-assembled
// PurchaseOrder object a single point read instead of a SQL join.
resource purchaseOrdersContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'purchase_orders'
  properties: {
    resource: {
      id: 'purchase_orders'
      partitionKey: {
        paths: ['/vendor_id']
        kind: 'Hash'
      }
    }
  }
}

// dbo.review_assignments. Partition key matches the SQL PK exactly. FR-062's expiry sweep
// (find_expired) becomes a cross-partition query — acceptable, it's an infrequent timer
// job, not a user-facing path.
resource reviewAssignmentsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'review_assignments'
  properties: {
    resource: {
      id: 'review_assignments'
      partitionKey: {
        paths: ['/invoice_id']
        kind: 'Hash'
      }
    }
  }
}

// dbo.corrections. The FR-071 training-set builder's "unused corrections" query becomes
// cross-partition — same reasoning as review_assignments, it's an offline batch job.
resource correctionsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'corrections'
  properties: {
    resource: {
      id: 'corrections'
      partitionKey: {
        paths: ['/invoice_id']
        kind: 'Hash'
      }
    }
  }
}

// dbo.audit_log. Immutability (Article VIII / T-046's done-when) is NOT enforced here —
// it's enforced by the restricted RBAC role below, which is the Cosmos equivalent of the
// SQL DENY UPDATE/DELETE grant in the migration this replaces.
resource auditLogContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'audit_log'
  properties: {
    resource: {
      id: 'audit_log'
      partitionKey: {
        paths: ['/entity_id']
        kind: 'Hash'
      }
    }
  }
}

// dbo.model_registry.
resource modelRegistryContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'model_registry'
  properties: {
    resource: {
      id: 'model_registry'
      partitionKey: {
        paths: ['/model_id']
        kind: 'Hash'
      }
    }
  }
}

// ============================================================================
// RBAC — Cosmos data-plane roles are a separate system from Microsoft.Authorization.
// Built-in role definition GUIDs are fixed, the same across every Cosmos account.
// ============================================================================

var dataContributorRoleId = '00000000-0000-0000-0000-000000000002'

// Constitution Article VIII / T-046 done-when: "an UPDATE (replace/delete) against
// audit_log as the application role is rejected." create+read only, no replace/upsert/
// delete — a bug or compromised credential cannot alter history, matching what the SQL
// DENY UPDATE/DELETE grant guaranteed.
resource auditLogWriterRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2024-05-15' = {
  parent: account
  name: guid(account.id, 'audit-log-writer')
  properties: {
    roleName: 'audit-log-writer'
    type: 'CustomRole'
    assignableScopes: [account.id]
    permissions: [
      {
        dataActions: [
          'Microsoft.DocumentDB/databaseAccounts/readMetadata'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/create'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/read'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/executeQuery'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/readChangeFeed'
        ]
      }
    ]
  }
}

// The app identity gets the built-in Data Contributor role, scoped per-container (not at
// the account or database level) so audit_log is never covered by it — only the restricted
// role above applies there. Cosmos RBAC evaluation unions every matching assignment, so a
// broader account-level grant here would silently defeat the audit_log restriction.
resource appDataContributorAssignments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = [
  for name in nonAuditContainerNames: {
    parent: account
    name: guid(account.id, principalId, name, 'DataContributor')
    properties: {
      roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributorRoleId}'
      principalId: principalId
      scope: '${account.id}/dbs/${databaseName}/colls/${name}'
    }
  }
]

resource appAuditLogAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: account
  name: guid(account.id, principalId, 'audit_log', 'AuditLogWriter')
  properties: {
    roleDefinitionId: auditLogWriterRole.id
    principalId: principalId
    scope: '${account.id}/dbs/${databaseName}/colls/audit_log'
  }
}

// Local development: full data-plane access at the account level, same shape as the
// former SQL admin login (sqlAdminLoginObjectId) — not scoped per-container, since this is
// a human operator, not the application's own least-privilege identity.
resource devDataContributorAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: account
  name: guid(account.id, cosmosDataAccessPrincipalId, 'DataContributor')
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributorRoleId}'
    principalId: cosmosDataAccessPrincipalId
    scope: account.id
  }
}

output accountEndpoint string = account.properties.documentEndpoint
output accountName string = account.name
output databaseName string = database.name
