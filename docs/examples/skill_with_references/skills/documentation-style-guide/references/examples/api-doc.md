# API Documentation Example

## `create_user(email, name, role)`

Creates a new user account in the system.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `email` | string | Yes | User's email address |
| `name` | string | Yes | User's display name |
| `role` | string | No | User role (default: "user") |

### Returns

```json
{
  "id": "usr_12345",
  "email": "user@example.com",
  "name": "Jane Doe",
  "role": "user",
  "created_at": "2024-01-15T10:30:00Z"
}
```

### Example

```python
from myapi import create_user

# Create a basic user
user = create_user("alice@example.com", "Alice Smith")
print(f"Created user: {user.id}")

# Create an admin user
admin = create_user(
    "bob@example.com",
    "Bob Jones",
    role="admin"
)
```

### Errors

| Code | Description |
|------|-------------|
| `400` | Invalid email format |
| `409` | Email already exists |
| `422` | Name contains invalid characters |

!!! note
    User IDs are generated automatically and cannot be specified.
