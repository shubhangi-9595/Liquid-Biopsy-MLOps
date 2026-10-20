// The application: API (Azure Container App) + daily drift check (Container Apps scheduled job).
// Deployed by the CD pipeline on every release with the new image tag:
//   az deployment group create -g <rg> -f infra/bicep/app.bicep -p image=<acr>/liquid-biopsy-api:<sha>
// It only references the foundation created by main.bicep (same naming rule), so the
// pipeline needs no role-assignment rights.

targetScope = 'resourceGroup'

@description('Full image reference, e.g. acrlbxxxx.azurecr.io/liquid-biopsy-api:1a2b3c4')
param image string

param location string = resourceGroup().location

@description('Daily drift check schedule (cron, UTC).')
param driftSchedule string = '0 6 * * *'

param tags object = {
  project: 'liquid-biopsy-mlops'
}

var suffix = uniqueString(resourceGroup().id)

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: 'acrlb${suffix}'
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: 'kv-lb-${suffix}'
}

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-lb-app'
}

resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: 'cae-lb-${suffix}'
}

// Secrets are Key Vault references: Azure fetches them with the managed identity,
// so the values never appear in this template, the pipeline, or the portal.
var secrets = [
  {
    name: 'database-url'
    keyVaultUrl: '${keyVault.properties.vaultUri}secrets/database-url'
    identity: appIdentity.id
  }
  {
    name: 'appinsights-connection-string'
    keyVaultUrl: '${keyVault.properties.vaultUri}secrets/appinsights-connection-string'
    identity: appIdentity.id
  }
]

var registries = [
  {
    server: acr.properties.loginServer
    identity: appIdentity.id
  }
]

var commonEnv = [
  { name: 'DATABASE_URL', secretRef: 'database-url' }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-connection-string' }
  { name: 'DB_CONNECT_ATTEMPTS', value: '20' }
]

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-liquid-biopsy'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${appIdentity.id}': {} }
  }
  properties: {
    environmentId: containerAppsEnv.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: registries
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(commonEnv, [{ name: 'RUN_MIGRATIONS', value: 'true' }])
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 6
              failureThreshold: 10
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 10
            }
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0 // scale to zero when idle: no cost while nobody calls the API
        maxReplicas: 2
        rules: [
          {
            name: 'http-load'
            http: { metadata: { concurrentRequests: '20' } }
          }
        ]
      }
    }
  }
}

resource driftJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'caj-lb-drift'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${appIdentity.id}': {} }
  }
  properties: {
    environmentId: containerAppsEnv.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 600
      replicaRetryLimit: 1
      scheduleTriggerConfig: {
        cronExpression: driftSchedule
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: registries
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'drift'
          image: image
          command: ['python', '-m', 'app.drift']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: concat(commonEnv, [{ name: 'RUN_MIGRATIONS', value: 'false' }])
        }
      ]
    }
  }
}

output url string = 'https://${api.properties.configuration.ingress.fqdn}'
output driftJobName string = driftJob.name
