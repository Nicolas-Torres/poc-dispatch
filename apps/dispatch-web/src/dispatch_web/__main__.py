from __future__ import annotations

import argparse
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the interactive mine demo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser.")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        webbrowser.open(url)
    print(f"demo at {url}")
    uvicorn.run("dispatch_web.api:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
