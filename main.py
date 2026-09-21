import json
import os
import shutil
import sys

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())
os.environ["LLM_PROVIDER"] = "wandb"  # Tumble is W&B-only by design, regardless of what's in .env

from llm_client import get_client_and_model  # noqa: E402
from tools import TOOL_FUNCTIONS, TOOL_SCHEMAS, WORKSPACE_ROOT  # noqa: E402
from trajectory import Trajectory, replay_file  # noqa: E402

ROUND_BUDGET = 12
MAX_TURNS = 40


def _safe_parse_args(raw: str) -> tuple[dict | None, str | None]:
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, str(e)

SYSTEM_PROMPT = f"""You are an autonomous ML research scientist. Your job is to improve the
validation accuracy of a classifier on the scikit-learn breast-cancer dataset,
defined in train.py, by iteratively editing it and running experiments.

Rules:
- train.py must keep printing exactly one line "METRIC: <float>" as its last
  output line -- that's how your score is read. The train/test split's
  random_state must stay 42 so scores are comparable across attempts.
- Use read_file to see the current train.py before changing it.
- Use write_file to propose one change at a time, then call run_experiment
  with a one-sentence description of what you changed.
- run_experiment automatically keeps your change if it beats the best score
  so far, or reverts it back to the last kept version if it doesn't (or if
  it errors). You do not decide this -- the harness does, deterministically.
- Use get_history any time to see every attempt so far so you don't repeat
  yourself or lose track of what's already been tried.
- You have a budget of {ROUND_BUDGET} experiments (run_experiment calls).
  Track this yourself with get_history. Once you've used your budget, or you
  believe further iteration won't meaningfully help, write a final research
  report to REPORT.md (via write_file) summarizing what you tried, what
  worked and what didn't, and your final best score and why -- then stop
  (reply with plain text and no further tool calls).
"""


def run(prompt: str) -> None:
    client, model = get_client_and_model()
    provider = os.environ.get("LLM_PROVIDER", "wandb")
    print(f"[provider={provider} model={model}]\n")

    trajectory = Trajectory(provider, model)
    trajectory.log_system(SYSTEM_PROMPT)
    trajectory.log_user(prompt)
    print(f"[trajectory] logging this run to {trajectory.path}\n")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    for turn in range(1, MAX_TURNS + 1):
        response = client.chat.completions.create(model=model, messages=messages, tools=TOOL_SCHEMAS)
        assistant_message = response.choices[0].message
        parsed_calls = [(c, *_safe_parse_args(c.function.arguments)) for c in (assistant_message.tool_calls or [])]

        # Sanitize before storing: a malformed arguments string must never be
        # resent to the API in the conversation history on a later turn, or
        # the API itself rejects the *whole request* server-side (not just
        # our own local json.loads), which would crash every future turn too.
        dumped = assistant_message.model_dump(exclude_none=True)
        if dumped.get("tool_calls"):
            for tc, (_, _, err) in zip(dumped["tool_calls"], parsed_calls):
                if err is not None:
                    tc["function"]["arguments"] = "{}"
        messages.append(dumped)

        tool_calls_log = None
        if parsed_calls:
            tool_calls_log = [
                {"id": c.id, "name": c.function.name, "args": args if args is not None else {"_parse_error": err}}
                for c, args, err in parsed_calls
            ]
        trajectory.log_assistant(assistant_message.content, tool_calls_log)

        if not assistant_message.tool_calls:
            print(assistant_message.content)
            trajectory.close()
            _finalize_artifacts()
            return

        for call, args, parse_error in parsed_calls:
            fn_name = call.function.name

            if parse_error is not None:
                # A model can emit malformed tool-call JSON (e.g. a corrupted
                # token mid-generation). That must never crash the harness --
                # tell the model so it can retry with valid arguments instead.
                result = f"Error: your arguments for {fn_name} were not valid JSON ({parse_error}). Please retry this tool call with valid JSON arguments."
                print(f"[turn {turn}] tool call: {fn_name}(<malformed JSON>)")
                print(f"    -> {result}")
                trajectory.log_tool_result(fn_name, {}, result)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                continue

            print(f"[turn {turn}] tool call: {fn_name}({args})")

            result = TOOL_FUNCTIONS[fn_name](**args)
            preview = result if len(result) <= 300 else result[:300] + " [truncated]"
            print(f"    -> {preview}")

            trajectory.log_tool_result(fn_name, args, result)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    print(f"\n[stopped after {MAX_TURNS} turns without a final answer]")
    trajectory.close()
    _finalize_artifacts()


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


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--replay":
        replay_file(sys.argv[2])
        return

    prompt = " ".join(sys.argv[1:]) or (
        "Improve the validation accuracy of the classifier in train.py as much as you can "
        f"within your budget of {ROUND_BUDGET} experiments, then write your final report."
    )
    run(prompt)


if __name__ == "__main__":
    main()
