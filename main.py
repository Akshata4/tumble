import argparse
import os
import shutil

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())
os.environ["LLM_PROVIDER"] = "wandb"  # Tumble is W&B-only by design, regardless of what's in .env

from agent_loop import run_agent  # noqa: E402
from dataset_preview import build_preview  # noqa: E402
from prompts import default_prompt, build_system_prompt  # noqa: E402
from sandbox_exec import SandboxSession  # noqa: E402
from tools import TOOL_SCHEMAS, Workspace  # noqa: E402
from trajectory import Trajectory, replay_file  # noqa: E402

WORKSPACE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".workspace")


def _print_event(event: dict) -> None:
    """The CLI's view of a run: subscribed to the Trajectory so the exact
    same event stream a server session would push over SSE prints here."""
    etype = event["type"]
    turn_prefix = f"[turn {event['turn']}] " if event.get("turn") is not None else ""

    if etype == "meta":
        print(f"[provider={event['provider']} model={event['model']}]\n")
    elif etype == "assistant":
        tool_calls = event.get("tool_calls") or []
        for call in tool_calls:
            print(f"{turn_prefix}tool call: {call['name']}({call['args']})")
        if not tool_calls and event.get("content"):
            print(event["content"])
    elif etype == "tool":
        result = event["result"]
        preview = result if len(result) <= 300 else result[:300] + " [truncated]"
        print(f"    -> {preview}")
    elif etype == "notice":
        print(f"\n[{event['content']}]")


def _finalize_artifacts() -> None:
    """Copy the winning script and report out of the gitignored, churn-heavy
    .workspace/ into the project root as the committed deliverable snapshot."""
    root = os.path.dirname(os.path.abspath(__file__))
    train_src = os.path.join(WORKSPACE_ROOT, "train.py")
    report_src = os.path.join(WORKSPACE_ROOT, "REPORT.md")

    if os.path.exists(train_src):
        shutil.copyfile(train_src, os.path.join(root, "best_train.py"))
        print(f"\n[finalize] copied {train_src} -> best_train.py")
    if os.path.exists(report_src):
        shutil.copyfile(report_src, os.path.join(root, "REPORT.md"))
        print(f"[finalize] copied {report_src} -> REPORT.md")
    else:
        print("\n[finalize] no REPORT.md was written by the agent")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tumble: an autonomous ML research agent.")
    parser.add_argument("--replay", metavar="TRAJECTORY_FILE", help="Replay a saved trajectory file instead of running.")
    parser.add_argument("--dataset", metavar="CSV_PATH", help="Path to the CSV dataset to optimize against.")
    parser.add_argument("--target", metavar="COLUMN", help="Name of the target column in the dataset.")
    parser.add_argument(
        "--metric", default="accuracy", help="Name of the metric train.py should compute and print (default: accuracy)."
    )
    parser.add_argument(
        "--minimize", action="store_true", help="Lower values of --metric are better (default: higher is better)."
    )
    parser.add_argument("prompt", nargs="*", help="Extra natural-language instructions for the agent.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    if args.replay:
        replay_file(args.replay)
        return

    if not args.dataset or not args.target:
        build_arg_parser().error("--dataset and --target are required (unless using --replay)")

    sandbox = SandboxSession(dataset_path=args.dataset, on_log=print)
    workspace = Workspace(WORKSPACE_ROOT, sandbox, session_id="cli")
    workspace.init(args.dataset, args.target, args.metric, args.minimize)

    preview = build_preview(args.dataset, args.target)
    system_prompt = build_system_prompt(preview, args.target, args.metric, args.minimize)
    prompt = " ".join(args.prompt) or default_prompt(args.metric)

    trajectory = Trajectory()
    trajectory.subscribe(_print_event)
    print(f"[trajectory] logging this run to {trajectory.path}\n")

    try:
        run_agent(prompt, system_prompt, TOOL_SCHEMAS, workspace.tool_functions(), trajectory)
    finally:
        sandbox.close()

    _finalize_artifacts()


if __name__ == "__main__":
    main()
