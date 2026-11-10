# Private API BFF

This service is the backend-side bridge between a browser-facing frontend and
the private Cloud Run API. It proxies only `/api` routes, forwards the existing
app session cookie and CSRF/origin headers, and adds a short-lived Google ID
token using its attached Cloud Run service account. The browser never receives
Google credentials. The existing FastAPI API continues to enforce user and
admin authentication and authorization.

The BFF itself must be reachable by the browser, so its Cloud Run service is
deployed with public invocation. The API remains private. The frontend hosting
layer must route same-origin `/api` requests to this BFF; its normal HTML and
static assets remain hosted by the frontend provider. Keep the browser and API
on the same public origin so HttpOnly session cookies continue to work.

## IAM setup

Create a dedicated service account, then grant it invocation permission on the
backend API service:

```powershell
gcloud iam service-accounts create backend-bff `
  --display-name "Backend API BFF"

gcloud run services add-iam-policy-binding supreme-court-rag-api `
  --region asia-south1 `
  --member "serviceAccount:backend-bff@YOUR_PROJECT_ID.iam.gserviceaccount.com" `
  --role roles/run.invoker
```

Use the same service account in the BFF deployment. Do not create or store a
service-account key. The BFF gets ID tokens from the Cloud Run metadata server.

## Build and deploy

From this directory, deploy using the actual backend Cloud Run URL and service
account email:

```powershell
gcloud builds submit . --config cloudbuild.yaml `
  --substitutions=_BFF_SERVICE_ACCOUNT=backend-bff@YOUR_PROJECT_ID.iam.gserviceaccount.com,_BACKEND_URL=https://YOUR_BACKEND_SERVICE.run.app
```

The backend's `CORS_ORIGINS` must contain the exact HTTPS origin used by the
browser frontend. The BFF preserves that browser `Origin` for the backend's
existing trusted-origin and CSRF checks. The BFF Cloud Run URL is not intended
to be used directly by the browser when the frontend has a different origin;
configure the frontend host to route `/api` to the BFF.
