from fastapi import FastAPI

app = FastAPI(title="CI/CD Demo Application")


@app.get("/")
def home():
    return {"message": "CI/CD demo application is running"}


@app.get("/health")
def health():
    return {"status": "healthy"}
