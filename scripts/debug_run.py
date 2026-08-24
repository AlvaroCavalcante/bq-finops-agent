"""Run the BQ FinOps ADK workflow locally with step-level debug output.

Preflight-checks Application Default Credentials, then executes the same
root agent used by the playground, printing every agent/LLM/tool event.

Usage (from repo root):

    uv run python scripts/debug_run.py --auth-only
    uv run python scripts/debug_run.py "SELECT 1"
    uv run python scripts/debug_run.py --step --verbose "SELECT 1"

On Windows, if the project path has non-ASCII characters:

    set UV_PROJECT_ENVIRONMENT=C:\\venvs\\bq-finops-agent
    uv run python scripts/debug_run.py --auth-only
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.adk.plugins import DebugLoggingPlugin, LoggingPlugin
from google.adk.runners import InMemoryRunner
from google.auth import default as google_auth_default
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.auth.transport.requests import Request
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQL = (
    "SELECT word, word_count "
    "FROM `bigquery-public-data.samples.shakespeare` "
    "WHERE corpus = 'hamlet' "
    "ORDER BY word_count DESC "
    "LIMIT 20"
)

def _auth_fix() -> str:
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or "YOUR_GCP_PROJECT"
    return f"""\
ADC refresh failed (invalid_grant). The playground is not reaching Gemini
because Application Default Credentials cannot mint a new access token.

Fix (run in a terminal, then retry this script / the playground):

  gcloud auth application-default login --project={project}
  gcloud auth application-default set-quota-project {project}

If it still fails, revoke and login again:

  gcloud auth application-default revoke
  gcloud auth application-default login --project={project}
"""


def _load_env() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)
    else:
        print(f"WARNING: {env_path} not found. Copy .env.example to .env.")


def check_adc(*, refresh: bool = True) -> None:
    """Validate ADC before starting the agent. Exits 2 on auth failure."""
    print("=== ADC preflight ===")
    print(f"GOOGLE_CLOUD_PROJECT={os.getenv('GOOGLE_CLOUD_PROJECT')}")
    print(f"GOOGLE_CLOUD_LOCATION={os.getenv('GOOGLE_CLOUD_LOCATION')}")
    print(f"BQ_LOCATION={os.getenv('BQ_LOCATION')}")
    print(f"GOOGLE_GENAI_USE_VERTEXAI={os.getenv('GOOGLE_GENAI_USE_VERTEXAI')}")
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    print(f"GOOGLE_APPLICATION_CREDENTIALS={creds_path or '(unset, using ADC file)'}")

    try:
        creds, project = google_auth_default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except DefaultCredentialsError as exc:
        print(f"ERROR: no Application Default Credentials: {exc}")
        print(_auth_fix())
        raise SystemExit(2) from exc

    print(f"cred_type={type(creds).__name__}")
    print(f"detected_project={project}")
    print(f"quota_project={getattr(creds, 'quota_project_id', None)}")
    print(f"expired={getattr(creds, 'expired', None)}")
    print(f"valid={getattr(creds, 'valid', None)}")
    print(f"has_refresh_token={bool(getattr(creds, 'refresh_token', None))}")

    if not refresh:
        print("ADC loaded (refresh skipped).")
        return

    try:
        creds.refresh(Request())
    except RefreshError as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        print(_auth_fix())
        raise SystemExit(2) from exc

    print("ADC refresh: ok")
    print()


def _format_event(event) -> str:
    node = getattr(event, "node_name", "") or ""
    path = ""
    if getattr(event, "node_info", None) is not None:
        path = event.node_info.path or ""
    author = event.author or "?"
    bits: list[str] = [f"author={author}"]
    if node:
        bits.append(f"node={node}")
    if path:
        bits.append(f"path={path}")
    if event.error_code:
        bits.append(f"ERROR {event.error_code}: {event.error_message}")
    if event.actions and event.actions.route is not None:
        bits.append(f"route={event.actions.route}")
    if event.actions and event.actions.state_delta:
        bits.append(f"state_delta={list(event.actions.state_delta)}")
    fcs = event.get_function_calls()
    if fcs:
        bits.append("tools=" + ",".join(fc.name or "?" for fc in fcs))
    frs = event.get_function_responses()
    if frs:
        bits.append("tool_results=" + ",".join(fr.name or "?" for fr in frs))
    if event.output is not None:
        preview = str(event.output)
        if len(preview) > 240:
            preview = preview[:240] + "..."
        bits.append(f"output={preview!r}")
    return " | ".join(bits)


async def run_agent(prompt: str, *, step: bool, debug_file: Path) -> int:
    from app.agent import app as adk_app

    runner = InMemoryRunner(
        app=adk_app,
        plugins=[
            LoggingPlugin(),
            DebugLoggingPlugin(output_path=str(debug_file)),
        ],
    )
    session = await runner.session_service.create_session(
        app_name=adk_app.name,
        user_id="debug-user",
    )
    print(f"=== run session_id={session.id} app={adk_app.name} ===")
    print(f"prompt: {prompt[:200]}{'...' if len(prompt) > 200 else ''}")
    print(f"yaml log: {debug_file}")
    print()

    message = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    event_index = 0
    try:
        async for event in runner.run_async(
            user_id="debug-user",
            session_id=session.id,
            new_message=message,
        ):
            event_index += 1
            print(f"[{event_index:03d}] {_format_event(event)}")
            if step:
                input("    (Enter to continue) ")
    except RefreshError as exc:
        print(f"ERROR during run: {type(exc).__name__}: {exc}")
        print(_auth_fix())
        return 2
    except Exception as exc:
        print(f"ERROR during run: {type(exc).__name__}: {exc}")
        logging.exception("ADK run failed")
        return 1

    session = await runner.session_service.get_session(
        app_name=adk_app.name,
        user_id="debug-user",
        session_id=session.id,
    )
    print()
    print("=== session.state keys ===")
    if session is not None:
        for key in session.state:
            value = session.state.get(key)
            preview = str(value)
            if len(preview) > 160:
                preview = preview[:160] + "..."
            print(f"  {key}: {preview}")
    print(f"done. full YAML dump: {debug_file}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Debug-run the BQ FinOps ADK workflow."
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=DEFAULT_SQL,
        help="User prompt / SQL to send to the agent.",
    )
    parser.add_argument(
        "--auth-only",
        action="store_true",
        help="Only check Application Default Credentials, then exit.",
    )
    parser.add_argument(
        "--skip-auth-check",
        action="store_true",
        help="Skip the ADC refresh preflight.",
    )
    parser.add_argument(
        "--step",
        action="store_true",
        help="Pause after each runner event (press Enter).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logs for google.adk (node scheduling, LLM flow).",
    )
    parser.add_argument(
        "--debug-file",
        default=str(ROOT / ".adk" / "debug_run.yaml"),
        help="YAML path written by DebugLoggingPlugin.",
    )
    return parser.parse_args()


def main() -> int:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    args = parse_args()
    _load_env()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("google_adk").setLevel(
        logging.DEBUG if args.verbose else logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("google.auth").setLevel(logging.INFO)

    check_adc(refresh=not args.skip_auth_check)
    if args.auth_only:
        return 0

    debug_file = Path(args.debug_file)
    debug_file.parent.mkdir(parents=True, exist_ok=True)
    return asyncio.run(run_agent(args.prompt, step=args.step, debug_file=debug_file))


if __name__ == "__main__":
    raise SystemExit(main())
