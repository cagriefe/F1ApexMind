# Security

ApexMind has no accounts and no cloud component: it reads UDP packets from your game and, optionally, serves a small dashboard.

- The dashboard (`--web`) listens on `localhost` only by default. With `--web-host 0.0.0.0` (for example to open it on a phone) anyone on the same network can read your live telemetry and download the bundled Python package; there is no authentication, so only do this on a network you trust.
- The web app analyses recordings in your browser; files are never uploaded. It loads the Pyodide runtime from the jsDelivr CDN.
- The optional LLM polish talks to a local Ollama server only.

To report a vulnerability, open a GitHub issue; for sensitive reports use GitHub's private vulnerability reporting on this repository if it is enabled.
