"""Entrypoint: python -m demo"""
import uvicorn

from .server.app import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
