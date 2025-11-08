"""Expose the FastAPI app and CLI hook for uvicorn."""

from src.main import app  # noqa: F401

if __name__ == "__main__":
    from src.server import run

    run()
