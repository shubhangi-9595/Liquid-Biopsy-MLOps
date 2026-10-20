// Optional Azure Kubernetes Service cluster for the GitOps (Argo CD) deployment path.
// Deployed by `infra/aks.sh up`; stop it with `infra/aks.sh stop` when not in use,
// because AKS nodes are billed for every hour they run.
//
//   - Workload identity: pods get an Azure identity without any stored password
//   - Key Vault CSI driver: secrets are mounted from Key Vault (see k8s/overlays/aks)
//   - The cluster pulls images from the project's container registry (AcrPull)

targetScope = 'resourceGroup'

param location string = resourceGroup().location

@description('VM size of the single node. B2ms = 2 vCPU / 8 GB: enough for the API, Argo CD and system pods.')
param nodeVmSize string = 'Standard_B2ms'

@minValue(1)
@maxValue(3)
param nodeCount int = 1

param tags object = {
  project: 'liquid-biopsy-mlops'
}

var suffix = uniqueString(resourceGroup().id)
var k8sNamespace = 'liquid-biopsy'
var k8sServiceAccount = 'liquid-biopsy-api'

var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: 'acrlb${suffix}'
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: 'kv-lb-${suffix}'
}

resource aks 'Microsoft.ContainerService/managedClusters@2024-09-01' = {
  name: 'aks-lb-${suffix}'
  location: location
  tags: tags
  sku: {
    name: 'Base'
    tier: 'Free'
  }
  identity: { type: 'SystemAssigned' }
  properties: {
    dnsPrefix: 'aks-lb-${suffix}'
    agentPoolProfiles: [
      {
        name: 'system'
        mode: 'System'
        count: nodeCount
        vmSize: nodeVmSize
        osType: 'Linux'
        type: 'VirtualMachineScaleSets'
      }
    ]
    networkProfile: {
      networkPlugin: 'azure'
      networkPluginMode: 'overlay'
      loadBalancerSku: 'standard'
    }
    oidcIssuerProfile: { enabled: true }
    securityProfile: {
      workloadIdentity: { enabled: true }
    }
    addonProfiles: {
      azureKeyvaultSecretsProvider: {
        enabled: true
        config: { enableSecretRotation: 'true' }
      }
    }
  }
}

// The cluster's kubelet identity may pull images from the registry
resource kubeletAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, aks.id, roles.acrPull)
  scope: acr
  properties: {
    principalId: aks.properties.identityProfile.kubeletidentity.objectId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

// Identity used by the API pods (through workload identity) to read Key Vault secrets
resource workloadIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-lb-aks-workload'
  location: location
  tags: tags
}

// Trust tokens issued by this cluster for exactly one Kubernetes service account
resource federatedCredential 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: workloadIdentity
  name: 'aks-${k8sNamespace}-${k8sServiceAccount}'
  properties: {
    issuer: aks.properties.oidcIssuerProfile.issuerURL
    subject: 'system:serviceaccount:${k8sNamespace}:${k8sServiceAccount}'
    audiences: ['api://AzureADTokenExchange']
  }
}

resource workloadSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, workloadIdentity.id, roles.keyVaultSecretsUser)
  scope: keyVault
  properties: {
    principalId: workloadIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
  }
}

output aksName string = aks.name
output keyVaultName string = keyVault.name
output tenantId string = subscription().tenantId
output workloadClientId string = workloadIdentity.properties.clientId
output acrLoginServer string = acr.properties.loginServer
