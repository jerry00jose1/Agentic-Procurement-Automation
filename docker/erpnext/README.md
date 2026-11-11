# ERPNext local stack

This directory vendors the official Frappe disposable demo Compose file (`pwd.yml`) from [`frappe/frappe_docker`](https://github.com/frappe/frappe_docker).

- Image: `frappe/erpnext:v16.54.0`
- UI: http://localhost:8080
- Default login: `Administrator` / `admin`
- Site name header: `frontend`

Start with:

```bash
make up-erpnext
make wait-erpnext
```

This stack is for local FDE demos only. Do not treat it as production. Data lives in Docker volumes under project name `agentic-procurement`.
