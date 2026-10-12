"""The propose -> execute -> evaluate -> keep/revert loop, independent of
who's driving it (CLI process or a FastAPI background thread). All
observability goes through `trajectory` -- this function itself never
prints or otherwise assumes a terminal is attached."""

import json
import os
import time

from budget import DailyBudget
from llm_client import get_client_and_model
from trajectory import Trajectory

MAX_TURNS = 40


def _safe_parse_args(raw: str) -> tuple[dict | None, str | None]:
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, str(e)


def run_agent(
    prompt: str,
    system_prompt: str,
    tool_schemas: list[dict],
    tool_functions: dict,
    trajectory: Trajectory,
    max_turns: int = MAX_TURNS,
    max_wall_clock_seconds: float | None = None,
    budget: DailyBudget | None = None,
) -> str:
    """Returns "completed" if the agent stopped itself with a final plain-text
    answer, "max_turns_exceeded" if the turn budget ran out first, or
    "time_limit_exceeded" if `max_wall_clock_seconds` ran out first."""
    client, model = get_client_and_model()
    trajectory.log_meta(os.environ.get("LLM_PROVIDER", "wandb"), model)
    trajectory.log_system(system_prompt)
    trajectory.log_user(prompt)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    start = time.monotonic()

    for turn in range(1, max_turns + 1):
        if max_wall_clock_seconds is not None and time.monotonic() - start > max_wall_clock_seconds:
            trajectory.log_notice(f"stopped after {max_wall_clock_seconds:.0f}s wall-clock limit")
            trajectory.close()
            return "time_limit_exceeded"

        if budget is not None and not budget.has_budget():
            trajectory.log_notice("stopped: the global daily budget is exhausted")
            trajectory.close()
            return "budget_exhausted"

        response = client.chat.completions.create(model=model, messages=messages, tools=tool_schemas)
        if budget is not None and response.usage is not None:
            budget.add_llm_tokens(response.usage.total_tokens)
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
        trajectory.log_assistant(assistant_message.content, tool_calls_log, turn=turn)

        if not assistant_message.tool_calls:
            trajectory.close()
            return "completed"

        for call, args, parse_error in parsed_calls:
            fn_name = call.function.name

            if parse_error is not None:
                # A model can emit malformed tool-call JSON (e.g. a corrupted
                # token mid-generation). That must never crash the harness --
                # tell the model so it can retry with valid arguments instead.
                result = f"Error: your arguments for {fn_name} were not valid JSON ({parse_error}). Please retry this tool call with valid JSON arguments."
                trajectory.log_tool_result(fn_name, {}, result, turn=turn)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                continue

            result = tool_functions[fn_name](**args)
            trajectory.log_tool_result(fn_name, args, result, turn=turn)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    trajectory.log_notice(f"stopped after {max_turns} turns without a final answer")
    trajectory.close()
    return "max_turns_exceeded"
