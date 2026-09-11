"""Bothost entry point for projects configured to run ``python app.py``."""

from playerok_minimal.app import App


if __name__ == "__main__":
    App().run()
