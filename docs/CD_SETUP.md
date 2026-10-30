# Continuous Deployment Setup Guide

## 1. Overview

The CD pipeline deploys the application to Google Cloud Run using GitHub Actions.

Deployment flow:

GitHub Actions → Docker Build → Google Artifact Registry → Google Cloud Run → Health Verification

Deployment is initially triggered manually. No automatic production deployment is configured.

## 2. GitHub Repository Variables

Navigate to:

GitHub Repository → Settings → Secrets and variables → Actions → Variables

Configure the following variables after the corresponding GCP resources have been created.

| Variable | Description |
|---|---|
| `GCP_PROJECT_ID` | GCP project ID selected for deployment |
| `GCP_REGION` | Region used for Artifact Registry and Cloud Run |
| `ARTIFACT_REGISTRY_REPOSITORY` | Name of the Docker repository in Artifact Registry |
| `CLOUD_RUN_SERVICE` | Cloud Run service name |
| `GCP_WIF_PROVIDER` | Full Workload Identity Federation provider resource name |
| `GCP_WIF_SERVICE_ACCOUNT` | Email address of the deployment service account |

Do not insert placeholder values into production configuration and assume they are valid.

## 3. Google Cloud Prerequisites

Before enabling deployment, configure the following:

1. Select the intended GCP project.
2. Enable the required Google Cloud APIs.
3. Create a Docker repository in Artifact Registry.
4. Configure Workload Identity Federation for GitHub Actions.
5. Restrict federation to the intended GitHub repository and deployment context.
6. Create a dedicated deployment service account.
7. Grant the minimum IAM permissions needed to publish images and deploy Cloud Run services.
8. Configure the Cloud Run runtime service account and required application permissions.

Do not use a permanent service-account private key in GitHub Actions.

## 4. Deployment Security

- Keep deployment separate from the CI workflow.
- Require an explicit deployment confirmation.
- Use immutable Git commit SHA tags for container images.
- Do not enable unauthenticated Cloud Run access unless explicitly required and approved.
- Do not store credentials or private keys in the repository.
- Pin third-party GitHub Actions to verified full commit SHAs before production use.
- Review IAM permissions before the first deployment.

## 5. Post-Deployment Verification

After deployment, verify:

- Cloud Run reports the intended revision as ready.
- The deployed container starts successfully.
- The application health endpoint returns the expected response.
- Application logs do not show startup or configuration errors.
- Required application integrations are accessible.

The health endpoint must be tested using an access method appropriate to the service's authentication configuration.

## 6. Rollback

Container images should be tagged with their Git commit SHA so that previous releases can be identified.

Before enabling automated rollback:

1. Define how a failed deployment is detected.
2. Identify the last known-good Cloud Run revision.
3. Define who can approve or initiate rollback.
4. Test rollback in a non-production environment.
5. Document how to verify service recovery.

## 7. Current Limitations

The workflow is a draft until the GitHub variables, GCP identity, IAM permissions, and target resources are configured.

A successful deployment must not be assumed until the deployed revision and application health have been verified.