# API conventions

## Versioning

Business endpoints will use the `/api/v1` prefix. Operational endpoints such as
`/health/live` and `/health/ready` remain unversioned.

## Error envelope

Every handled API error returns the same top-level shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": []
  }
}
```

- `code` is stable and intended for clients.
- `message` is safe to show to a user.
- `details` is optional structured context and must not expose secrets or internal
  exception text.

Unexpected server errors are logged with internal context, but production responses
must use a generic message and a correlation ID once observability is introduced.
