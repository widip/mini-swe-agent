import litellm
import re
import yaml
import time
from typing import Any
from jinja2 import StrictUndefined, Template
from minisweagent.models.litellm_model import LitellmModel, LitellmModelConfig
from minisweagent.models.utils.openai_multimodal import expand_multimodal_content
from minisweagent.exceptions import FormatError


class MytilusLoader(yaml.SafeLoader):
    """Custom YAML loader that tolerates Mytilus tags (!pip, !git, etc.) without failing."""
    pass

# Tolerates unknown tags
def construct_undefined(loader, node):
    return node

MytilusLoader.add_constructor(None, construct_undefined)
MytilusLoader.add_multi_constructor('!', lambda loader, suffix, node: (suffix, node))


class LitellmYamlModelConfig(LitellmModelConfig):
    command_tool_name: str = "mytilus"
    """Name of the tool that the agent thinks it's using (for logging/UI)."""
    action_regex: str = ""
    """Regex to extract YAML blocks from the model's response. If empty, the whole response is treated as YAML."""


class LitellmYamlModel(LitellmModel):
    """Model that treats its entire response as a single YAML-native command."""

    def __init__(self, **kwargs):
        super().__init__(config_class=LitellmYamlModelConfig, **kwargs)

    def _query(self, messages: list[dict[str, str]], **kwargs):
        # We override _query to ensure we DON'T pass tools, as we want raw YAML output.
        try:
            return litellm.completion(
                model=self.config.model_name,
                messages=messages,
                tools=None,
                timeout=60,
                **(self.config.model_kwargs | kwargs),
            )
        except litellm.exceptions.AuthenticationError as e:
            e.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            raise e

    def _parse_actions(self, response) -> list[dict]:
        """Treat the response as a YAML stream, validating each document."""
        content = (response.choices[0].message.content or "").strip()
        
        # Extract YAML from code fences if action_regex is provided
        if self.config.action_regex:
            matches = re.findall(self.config.action_regex, content, re.DOTALL)
            if matches:
                 # Join with stream separators to treat as a single YAML stream
                 content = "\n---\n".join(matches)

        # 1. Syntactic Validation of the whole stream
        try:
            # We use load_all to ensure the entire stream is syntactically valid YAML
            list(yaml.load_all(content, Loader=MytilusLoader))
        except yaml.YAMLError as e:
            # Raise FormatError so the main agent loop can catch it and tell the model what's wrong.
            error_msg = f"Your response is not a valid YAML stream:\n{e}"
            raise FormatError(
                {
                    "role": "user",
                    "content": Template(self.config.format_error_template, undefined=StrictUndefined).render(
                        error=error_msg
                    ),
                    "extra": {
                        "interrupt_type": "FormatError",
                        "model_response": content,
                        "error_details": str(e),
                    },
                }
            )

        # 2. Split the stream into individual documents (actions)
        # We split by '---' at the start of a line to treat each document as a separate command action.
        # This allows the agent to execute them sequentially and log observations for each.
        raw_docs = re.split(r'^---', content, flags=re.MULTILINE)
        
        actions = []
        for doc in raw_docs:
            doc = doc.strip()
            if not doc:
                continue
            actions.append({"command": doc})

        # 3. Limit to first 5 documents to prevent overly long responses
        if len(actions) > 5:
            actions = actions[:5]

        return actions

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        """Format observations, adding canonical YAML on failure."""
        observation_results = []
        actions = message.get("extra", {}).get("actions", [])
        for i, output in enumerate(outputs):
            cmd = actions[i].get("command", "") if i < len(actions) else ""
            # 1. Synthesize tokens for the relevant action (if failure)
            yaml_tokens = []
            if output.get("returncode", 0) != 0 and cmd:
                try:
                    tokens = list(yaml.scan(cmd))
                    yaml_tokens = []
                    for t in tokens:
                        if hasattr(t, 'value'):
                            yaml_tokens.append(f"{type(t).__name__}({t.value!r})")
                        else:
                            yaml_tokens.append(type(t).__name__)
                except Exception:
                    pass

            # 2. Render the observation template
            content = Template(self.config.observation_template, undefined=StrictUndefined).render(
                output=output, yaml_tokens=yaml_tokens, command=cmd, **(template_vars or {})
            )
            
            # 3. Augment exception_info for traceability
            exception_info = output.get("exception_info", "")
            if yaml_tokens:
                 # We still append to exception_info just in case the template doesn't show it or for logging
                 exception_info += f"\n\nTokens observed:\n{yaml_tokens}\n"

            msg: dict = {
                "role": "user",
                "content": content.strip(),
                "extra": {
                    "raw_output": output.get("output", ""),
                    "returncode": output.get("returncode"),
                    "timestamp": time.time(),
                    "exception_info": exception_info or None,
                    **output.get("extra", {}),
                },
            }
            observation_results.append(expand_multimodal_content(msg, pattern=self.config.multimodal_regex))
        return observation_results
