"""Agent (LLM) calls through any-llm: an agent is "provider:agent", e.g. zai:glm-5.3 or ollama:qwen3:8b.

Keys and endpoints come from each provider's usual environment variables (ZAI_API_KEY,
ZAI_BASE_URL, OLLAMA_HOST, OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY, ...).
"""


class RateLimited(Exception):
    pass


def chat(agent, system, user, thinking=None, timeout=None):
    """One single-shot call. Returns (reply text, usage dict); raises on failure."""
    # Imported here: any-llm takes over a second to import, and only `run` needs it.
    from any_llm import completion
    from any_llm.exceptions import RateLimitError
    try:
        # The timeout goes on the client: some providers (Ollama) have no per-request timeout.
        reply = completion(model=agent, client_args={"timeout": timeout}, reasoning_effort=thinking or "auto",
                           messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    except RateLimitError as e:
        raise RateLimited(str(e)) from e
    choice, usage = reply.choices[0], reply.usage
    if choice.finish_reason not in (None, "stop"):
        raise ValueError(f"the reply stopped early ({choice.finish_reason})")
    return choice.message.content or "", {
        "input_tokens": usage and usage.prompt_tokens, "output_tokens": usage and usage.completion_tokens}
