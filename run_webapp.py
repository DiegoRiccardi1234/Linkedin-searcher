"""Dev/container entry point.

The host must come from the environment: inside a container, binding to
127.0.0.1 makes the published port map to nothing — `docker compose up` yields a
container whose health check passes from the inside while being unreachable from
the outside. The Dockerfile sets JOBFINDER_HOST=0.0.0.0; locally the default
stays loopback, because this app holds the user's CV and API keys and has no
authentication.

Auto-reload is opt-in for the same reason: it shipped as True in the image,
spawning a file-watcher process in production.
"""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("JOBFINDER_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("JOBFINDER_RELOAD", "").lower() in ("1", "true", "yes"),
    )
