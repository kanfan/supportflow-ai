from app.worker import HEALTH_TASK_NAME, celery_app


def main() -> None:
    result = celery_app.send_task(HEALTH_TASK_NAME)
    payload = result.get(timeout=20)
    if payload != {"status": "ok"}:
        raise RuntimeError(f"Unexpected worker response: {payload!r}")
    print(f"Worker smoke test passed: {payload}")


if __name__ == "__main__":
    main()
