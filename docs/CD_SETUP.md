# Cloud Run deployment setup

The GitHub Actions deployment workflow runs its reusable CI workflow first.
Pull requests never receive GCP credentials. During the initial production
deployment, CD is available only through `workflow_dispatch` on `main` and
requires `confirm_deploy=yes`. The push trigger is intentionally disabled until
the initial deployment has been approved and verified. After that verification,
the `main` push trigger can be restored for future approved changes.

## GitHub Actions variables

Add these as repository Actions variables. Use the existing project and
Workload Identity Federation configuration and supply the actual runtime
service accounts, connector, frontend origin, and SMTP settings for this
environment.

| Variable | Value or source |
| --- | --- |
| `GCP_PROJECT_ID` | `sc-judgments-rag` |
| `GCP_REGION` | `asia-south1` |
| `ARTIFACT_REGISTRY_REPOSITORY` | `scj-cicd-repo` |
| `GCP_WIF_PROVIDER` | `projects/315039048199/locations/global/workloadIdentityPools/github-actions-pool/providers/github-provider` |
| `GCP_WIF_SERVICE_ACCOUNT` | `github-cloudrun-deployer@sc-judgments-rag.iam.gserviceaccount.com` |
| `CLOUD_RUN_API_SERVICE_ACCOUNT` | Existing least-privilege API runtime service account email |
| `CLOUD_RUN_BFF_SERVICE_ACCOUNT` | Existing BFF runtime service account email |
| `CLOUD_RUN_VPC_CONNECTOR` | Existing Serverless VPC Access connector name |
| `FRONTEND_ORIGIN` | Actual HTTPS origin of the current frontend host, without a path |
| `SMTP_HOST` | Approved production SMTP host |
| `SMTP_FROM_EMAIL` | Verified sender address for that SMTP service |

Do not put secret values in Actions variables. The workflow references these
existing Secret Manager secret IDs for the API: `backend-database-url`,
`backend-redis-url`, `backend-auth-challenge-secret`, `backend-groq-api-key`,
`backend-smtp-username`, and `backend-smtp-password`. Confirm that they exist
and that the API runtime service account can access them before enabling CD.

## Existing permissions and service wiring

The deployment service account must already have the documented Artifact
Registry Writer, Cloud Run Admin, and Service Account User permissions, plus
the configured Workload Identity User binding. The BFF runtime service account
must already be allowed to invoke the private API. The API runtime service
account must already have access to the required secrets and private network
dependencies. This workflow does not create IAM bindings, secrets, or network
resources.

The API service `supreme-court-rag-api` is private. The BFF service
`supreme-court-rag-bff` is publicly invokable as required by the documented
browser proxy design. The workflow builds the API from `backend/` and BFF from
`backend/bff/`, and tags both images with the commit SHA in
`asia-south1-docker.pkg.dev/sc-judgments-rag/scj-cicd-repo`. It does not yet
deploy the frontend. The frontend Dockerfile requires the actual BFF HTTPS
origin at build time, and the API requires the exact browser frontend origin
for CORS. Neither a frontend Cloud Run service nor a production origin is
currently configured in the inspected project. Do not replace these values
with placeholders. Add the frontend service after its actual service name and
origin are established, then validate the API CORS and BFF proxy path together.

Protect `main` with required CI checks and review before changes can merge. A
successful workflow file review does not verify GCP resources or constitute a
deployment; the first deployment must be reviewed in GitHub Actions and
verified against the Cloud Run revisions and health checks.
