# Architecture Scanner — SS Workspace Documentation

Complete technical documentation for the **SS Workspace Telegram Cloud Drive** project.
All diagrams use **Mermaid** syntax and render in GitHub, VS Code, and the Antigravity viewer.

---

## Files in This Folder

| # | File | What's Inside |
| :--- | :--- | :--- |
| 1 | [1_flow_diagram.md](./1_flow_diagram.md) | Master all-modules flowchart + 6 individual operation flows (Startup, Upload, Download, Todo, Auth, Sync) |
| 2 | [2_architecture_diagram.md](./2_architecture_diagram.md) | Full system architecture, component responsibility map, 6-worker upload pool diagram, 6-worker sliding window download diagram |
| 3 | [3_deployment_diagram.md](./3_deployment_diagram.md) | Production Render topology, CI/CD pipeline, env vars table, startup sequence, **combined all-module sequence diagram** |
| 4 | [4_how_to_use.md](./4_how_to_use.md) | End-user guide: tab-by-tab features, upload progress, Telegram direct sync, admin commands |
| 5 | [5_module_reference.md](./5_module_reference.md) | Every Python file explained in plain English — functions, responsibilities, constants, data tables |
| 6 | [6_data_flow.md](./6_data_flow.md) | Data lifecycle sequence diagrams for Upload, Download, Todos, Notes, Sync, SQLite ER diagram, Auth flow, Demo vs Live mode |

---

## Project Module Map

```
telegram-cloud-drive/
├── config.py              → Credentials loader (.env → constants)
├── database.py            → SQLite CRUD (files, todos, notes, otp_sessions)
├── telegram_client.py     → MTProto engine (upload, download, messaging, OTP dispatch)
├── main.py                → FastAPI routes + dynamic OTP security + progress tracker
├── static/
│   ├── index.html         → Single-page app (Tailwind CSS, Vanilla JS)
│   └── favicon.svg        → Cloud + vault icon
├── requirements.txt       → Python dependencies
├── run_server.bat         → Local Windows startup script
├── launch_silent.vbs      → Background launcher (no terminal window)
├── test_app.py            → pytest automated tests (13 tests including Aikido security suite)
└── architecture-scanner/  ← YOU ARE HERE
    ├── README.md
    ├── 1_flow_diagram.md
    ├── 2_architecture_diagram.md
    ├── 3_deployment_diagram.md
    ├── 4_how_to_use.md
    ├── 5_module_reference.md
    └── 6_data_flow.md
```

---

## Quick Links

- **Live Site:** https://drive-ssworkspace.onrender.com
- **GitHub Repo:** https://github.com/120198subham/telegram-cloud-drive
- **Health Check:** https://drive-ssworkspace.onrender.com/api/status
