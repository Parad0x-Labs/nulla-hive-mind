from __future__ import annotations

import argparse

from apps.nulla_agent import NullaAgent
from core.compute_mode import ComputeModeDaemon
from core.hardware_tier import probe_machine, select_qwen_tier, tier_summary
from core.model_registry import ModelRegistry
from core.onboarding import get_agent_display_name, is_first_boot, run_onboarding_interactive
from core.provider_bootstrap import ensure_default_provider
from core.runtime_bootstrap import bootstrap_runtime_mode


def _print_prewarm_results(model_registry: ModelRegistry) -> None:
    for result in model_registry.prewarm_enabled_providers():
        provider_id = str(result.get("provider_id") or "unknown-provider")
        status = str(result.get("status") or "unknown")
        if result.get("ok") and status == "prewarmed":
            print(
                f"Model prewarm ready: {provider_id} | "
                f"keep_alive={result.get('keep_alive') or 'default'} | "
                f"load_duration={result.get('load_duration')}"
            )
            continue
        if result.get("ok"):
            print(f"Model prewarm skipped: {provider_id} | {result.get('reason') or status}")
            continue
        print(f"Model prewarm failed: {provider_id} | {result.get('error') or result.get('reason') or status}")


def _bootstrap_agent(*, persona_id: str, device: str) -> NullaAgent:
    boot = bootstrap_runtime_mode(
        mode="chat",
        force_policy_reload=True,
        resolve_backend=True,
    )

    if is_first_boot():
        run_onboarding_interactive()

    probe = probe_machine()
    tier = select_qwen_tier(probe)
    hw_info = tier_summary(probe)
    vram_part = f" ({hw_info['vram_gb']}GB VRAM)" if hw_info.get("vram_gb") else ""
    print(
        f"Hardware: {hw_info['accelerator']} | RAM {hw_info['ram_gb']}GB | "
        f"GPU {hw_info['gpu'] or 'none'}{vram_part}"
    )
    print(f"Selected model tier: {tier.tier_name} -> {tier.ollama_tag}")

    compute_daemon = ComputeModeDaemon(has_gpu=probe.accelerator != "cpu")
    compute_daemon.start()
    budget = compute_daemon.budget
    print(
        f"Compute mode: {budget.mode} | CPU threads: {budget.cpu_threads} | "
        f"GPU mem fraction: {budget.gpu_memory_fraction:.0%}"
    )

    model_registry = ModelRegistry()
    ensure_default_provider(model_registry, tier.ollama_tag)
    provider_warnings = model_registry.startup_warnings()
    if provider_warnings:
        print("Model provider warnings:")
        for warning in provider_warnings:
            print(f" - {warning}")
    _print_prewarm_results(model_registry)

    selection = boot.backend_selection
    if selection is None:
        raise RuntimeError("Chat bootstrap did not resolve a backend selection.")
    if selection.backend_name == "remote_only":
        print("No local model backend found. Starting in remote-first mode.")

    agent = NullaAgent(
        backend_name=selection.backend_name,
        device=device,
        persona_id=persona_id,
    )
    runtime = agent.start()
    display_name = get_agent_display_name()
    print(f"{display_name} is ready.")
    print(f"Backend: {runtime.backend_name} | Device: {runtime.device} | Persona: {runtime.persona_id}")
    print("Type /exit to quit.")
    return agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nulla-chat", description="Interactive local chat with NULLA.")
    parser.add_argument("--persona", default="default", help="Persona id")
    parser.add_argument("--device", default="openclaw", help="Session device hint")
    parser.add_argument("--platform", default="openclaw", help="Source platform label")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        agent = _bootstrap_agent(persona_id=str(args.persona), device=str(args.device))
    except Exception as exc:
        print(f"NULLA chat bootstrap failed: {exc}")
        return 1

    prompt_tag = get_agent_display_name().lower()
    source_context = {"surface": "channel", "platform": str(args.platform)}
    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return 0
        if not user_text:
            continue
        if user_text.lower() in {"/exit", "/quit", "exit", "quit"}:
            print("bye.")
            return 0
        if user_text.lower().startswith("/rename "):
            new_name = user_text[8:].strip()
            if not new_name:
                print("usage: /rename <new-name>")
                continue
            from core.onboarding import force_rename

            force_rename(new_name)
            prompt_tag = new_name.lower()
            print(f"name updated: {new_name}")
            continue
        if user_text.lower() in {"/credits", "/balance"}:
            from core.credit_ledger import reconcile_ledger
            from network.signer import get_local_peer_id

            recon = reconcile_ledger(get_local_peer_id())
            print(f"credits={recon.balance:.2f} entries={recon.entries} mode={recon.mode}")
            continue
        if user_text.lower() in {"/summary", "/status"}:
            from core.nulla_user_summary import build_user_summary, render_user_summary

            print(render_user_summary(build_user_summary()))
            continue
        try:
            result = agent.run_once(user_text, source_context=source_context)
        except Exception as exc:
            print(f"{prompt_tag}> [error] {exc}")
            continue
        response = str(result.get("response") or "").strip()
        confidence = float(result.get("confidence") or 0.0)
        print(f"{prompt_tag}> {response}")
        print(f"[confidence={confidence:.2f}]")


if __name__ == "__main__":
    raise SystemExit(main())
