from fastapi import FastAPI


app = FastAPI(
    title="SupportFlow AI",
    version="0.1.0",
)


@app.get("/health/live", tags=["health"])
async def live_health() -> dict[str, str]:
    return {"status": "ok"}
