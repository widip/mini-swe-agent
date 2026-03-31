#!/usr/bin/env python3

"""Run mini-SWE-agent on SWE-bench instances in batch mode."""
# Read this first: https://mini-swe-agent.com/latest/usage/swebench/  (usage docs)

import os
import concurrent.futures
import json
import random
import re
import threading
import time
import subprocess
import traceback
import tempfile
import shutil
from pathlib import Path

import typer
import dotenv
from jinja2 import StrictUndefined, Template
from rich.live import Live

from minisweagent import Environment
from minisweagent.agents.default import DefaultAgent
from minisweagent.config import builtin_config_dir, get_config_from_spec
from minisweagent.environments import get_environment
from minisweagent.models import get_model
from minisweagent.run.benchmarks.utils.batch_progress import RunBatchProgressManager
from minisweagent.utils.log import add_file_handler, logger
from minisweagent.utils.serialize import UNSET, recursive_merge

_HELP_TEXT = """Run mini-SWE-agent on SWEBench instances.

[not dim]
More information about the usage: [bold green]https://mini-swe-agent.com/latest/usage/swebench/[/bold green]
[/not dim]
"""

_CONFIG_SPEC_HELP_TEXT = """Path to config files, filenames, or key-value pairs.

[bold red]IMPORTANT:[/bold red] [red]If you set this option, the default config file will not be used.[/red]
So you need to explicitly set it e.g., with [bold green]-c swebench.yaml <other options>[/bold green]

Multiple configs will be recursively merged.

Examples:

[bold red]-c model.model_kwargs.temperature=0[/bold red] [red]You forgot to add the default config file! See above.[/red]

[bold green]-c swebench.yaml -c model.model_kwargs.temperature=0.5[/bold green]

[bold green]-c swebench.yaml -c agent.max_iterations=50[/bold green]
"""

DEFAULT_CONFIG_FILE = builtin_config_dir / "benchmarks" / "swebench.yaml"

DATASET_MAPPING = {
    "full": "princeton-nlp/SWE-Bench",
    "verified": "princeton-nlp/SWE-Bench_Verified",
    "lite": "princeton-nlp/SWE-Bench_Lite",
    "multimodal": "princeton-nlp/SWE-Bench_Multimodal",
    "multilingual": "swe-bench/SWE-Bench_Multilingual",
    "smith": "SWE-bench/SWE-smith",
    "_test": "klieret/swe-bench-dummy-test-dataset",
    "rebench": "nebius/SWE-rebench",
}

app = typer.Typer(rich_markup_mode="rich", add_completion=False)
_OUTPUT_FILE_LOCK = threading.Lock()


class ProgressTrackingAgent(DefaultAgent):
    """Simple wrapper around DefaultAgent that provides progress updates."""

    def __init__(self, *args, progress_manager: RunBatchProgressManager, instance_id: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_manager: RunBatchProgressManager = progress_manager
        self.instance_id = instance_id

    def query(self) -> dict:
        """Override query to log the model's response content or tool calls."""
        try:
            message = super().query()
        except Exception as e:
            logger.error(f"Error during model query: {e}")
            raise
        if content := message.get("content"):
            logger.info(f"Model response:\n{content}")
        for action in message.get("extra", {}).get("actions", []):
            if cmd := action.get("command"):
                # If we're using Mytilus YAML, call it YAML, otherwise call it command
                is_yaml = self.model.config.model_dump().get("model_class") == "litellm_yaml"
                label = "YAML (from tool call)" if is_yaml else "command"
                logger.info(f"Model {label}:\n{cmd}")
        return message

    def execute_actions(self, message: dict) -> list[dict]:
        """Override execute_actions to log environment output."""
        actions = message.get("extra", {}).get("actions", [])
        outputs = [self.env.execute(action) for action in actions]
        for output in outputs:
            if out_text := output.get("output"):
                logger.info(f"Environment response:\n{out_text}")
        
        obs_messages = self.model.format_observation_messages(message, outputs, self.get_template_vars())
        for obs_msg in obs_messages:
            logger.info(f"Observation sent to model:\n{obs_msg['content']}")
        return self.add_messages(*obs_messages)

    def step(self) -> dict:
        """Override step to provide progress updates."""
        self.progress_manager.update_instance_status(self.instance_id, f"Step {self.n_calls + 1:3d} (${self.cost:.2f})")
        return super().step()


def get_swebench_docker_image_name(instance: dict) -> str:
    """Get the image name for a SWEBench instance."""
    image_name = instance.get("image_name", None) or instance.get("docker_image", None)
    if image_name is None:
        # Docker doesn't allow double underscore, so we replace them with a magic token
        iid = instance["instance_id"]
        id_docker_compatible = iid.replace("__", "_1776_")
        image_name = f"docker.io/swebench/sweb.eval.x86_64.{id_docker_compatible}:latest".lower()
    return image_name


def get_sb_environment(config: dict, instance: dict, build: bool = False) -> Environment:
    env_config = config.setdefault("environment", {})
    env_config["environment_class"] = env_config.get("environment_class", "docker")
    image_name = get_swebench_docker_image_name(instance)

    if env_config.get("environment_class") == "docker":
        # Check if we're using Mytilus for the interpreter or explicitly requested enhancement
        use_mytilus = env_config.get("use_mytilus", False)
        if not use_mytilus and env_config.get("interpreter"):
            use_mytilus = "mytilus" in env_config["interpreter"]
        
        if use_mytilus:
            # We always want to build/use the mytilus-enhanced version for stability
            mytilus_path = os.getenv("MSWEA_MYTILUS_PATH", "/home/widip/mytilus")
            enhanced_image = f"{image_name}-mytilus-v16"
            
            # Check if the enhanced image exists
            try:
                subprocess.run(["docker", "image", "inspect", enhanced_image], check=True, capture_output=True)
                logger.info(f"Using existing enhanced image {enhanced_image}")
                image_name = enhanced_image
            except subprocess.CalledProcessError:
                # Not found, let's build it
                logger.info(f"Building mytilus-enhanced image {enhanced_image}...")
                
                # Ensure base image is present
                try:
                    subprocess.run(["docker", "image", "inspect", image_name], check=True, capture_output=True)
                except subprocess.CalledProcessError:
                    logger.info(f"Base image {image_name} not found. Pulling...")
                    subprocess.run(["docker", "pull", image_name], check=True)
                
                # Create a simple Dockerfile to bake in Mytilus with Python 3.13
                dockerfile = f"""
FROM {image_name}
COPY mytilus /mytilus
COPY uv /usr/local/bin/uv
# Install Python 3.13 and create a venv, then install mytilus into it
RUN /usr/local/bin/uv venv /opt/mytilus-venv --python 3.13 && \\
    /usr/local/bin/uv pip install --python /opt/mytilus-venv/bin/python discopy pyyaml watchdog nx-yaml mcp fastmcp && \\
    /usr/local/bin/uv pip install --python /opt/mytilus-venv/bin/python -e /mytilus
ENV PATH="/opt/mytilus-venv/bin:$PATH"
"""
                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp_path = Path(tmp_dir)
                    (tmp_path / "Dockerfile").write_text(dockerfile)
                    # Copy local mytilus source
                    if os.path.exists(mytilus_path):
                        shutil.copytree(
                            mytilus_path, 
                            tmp_path / "mytilus", 
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".venv*", ".git", "__pycache__", ".agent", ".codex")
                        )
                    else:
                        logger.error(f"Mytilus source not found at {mytilus_path}")
                        raise FileNotFoundError(f"Mytilus source not found at {mytilus_path}")
                    
                    # Copy local uv binary
                    uv_path = "/home/widip/.local/bin/uv"
                    if os.path.exists(uv_path):
                        shutil.copy2(uv_path, tmp_path / "uv")
                    else:
                        logger.warning(f"UV binary not found at {uv_path}. Attempting to pull it.")
                        # Fallback or error? Let's just try to get it if we can
                    
                    # Build the image
                    try:
                        subprocess.run(["docker", "build", "-t", enhanced_image, "."], cwd=tmp_dir, check=True, capture_output=True, text=True)
                    except subprocess.CalledProcessError as e:
                        logger.error(f"Docker build failed for {enhanced_image}:")
                        logger.error(e.stdout)
                        logger.error(e.stderr)
                        raise
                    image_name = enhanced_image

    if env_config["environment_class"] in ["docker", "swerex_modal"]:
        env_config["image"] = image_name
    elif env_config["environment_class"] in ["singularity", "contree"]:
        env_config["image"] = "docker://" + image_name

    env = get_environment(env_config)
    if startup_command := config.get("run", {}).get("env_startup_command"):
        startup_command = Template(startup_command, undefined=StrictUndefined).render(**instance)
        out = env.execute(startup_command, interpreter=["bash", "-c"])
        if out["returncode"] != 0:
            raise RuntimeError(f"Error executing startup command: {out}")
    return env


def update_preds_file(output_path: Path, instance_id: str, model_name: str, result: str):
    """Update the output JSON file with results from a single instance."""
    with _OUTPUT_FILE_LOCK:
        output_data = {}
        if output_path.exists():
            output_data = json.loads(output_path.read_text())
        output_data[instance_id] = {
            "model_name_or_path": model_name,
            "instance_id": instance_id,
            "model_patch": result,
        }
        output_path.write_text(json.dumps(output_data, indent=2))


def remove_from_preds_file(output_path: Path, instance_id: str):
    """Remove an instance from the predictions file."""
    if not output_path.exists():
        return
    with _OUTPUT_FILE_LOCK:
        output_data = json.loads(output_path.read_text())
        if instance_id in output_data:
            del output_data[instance_id]
            output_path.write_text(json.dumps(output_data, indent=2))


def process_instance(
    instance: dict,
    output_dir: Path,
    config: dict,
    progress_manager: RunBatchProgressManager,
    build: bool = False,
) -> None:
    """Process a single SWEBench instance."""
    instance_id = instance["instance_id"]
    instance_dir = output_dir / instance_id
    # avoid inconsistent state if something here fails and there's leftover previous files
    remove_from_preds_file(output_dir / "preds.json", instance_id)
    (instance_dir / f"{instance_id}.traj.json").unlink(missing_ok=True)
    # 1. Clean up Ollama context if using an Ollama model
    model_config = config.get("model", {})
    model_name = model_config.get("model_name", "")
    model = get_model(config=model_config)
    task = instance["problem_statement"]

    progress_manager.on_instance_start(instance_id)
    progress_manager.update_instance_status(instance_id, "Pulling/starting environment")

    agent = None
    exit_status = None
    result = None
    extra_info = {}

    try:
        env = get_sb_environment(config, instance, build=build)
        agent = ProgressTrackingAgent(
            model,
            env,
            progress_manager=progress_manager,
            instance_id=instance_id,
            **config.get("agent", {}),
        )
        info = agent.run(task)
        exit_status = info.get("exit_status")
        result = info.get("submission")
    except Exception as e:
        if isinstance(e, subprocess.CalledProcessError) and e.returncode == 130:
            logger.warning(f"Processing of instance {instance_id} was interrupted (SIGINT).")
        else:
            logger.error(f"Error processing instance {instance_id}: {e}", exc_info=True)
        exit_status, result = type(e).__name__, ""
        extra_info = {"traceback": traceback.format_exc(), "exception_str": str(e)}
    finally:
        if agent is not None:
            traj_path = instance_dir / f"{instance_id}.traj.json"
            agent.save(
                traj_path,
                {
                    "info": {
                        "exit_status": exit_status,
                        "submission": result,
                        **extra_info,
                    },
                    "instance_id": instance_id,
                },
            )
            logger.info(f"Saved trajectory to '{traj_path}'")
        update_preds_file(output_dir / "preds.json", instance_id, model.config.model_name, result)
        progress_manager.on_instance_end(instance_id, exit_status)


def filter_instances(
    instances: list[dict], *, filter_spec: str, slice_spec: str = "", shuffle: bool = False
) -> list[dict]:
    """Filter and slice a list of SWEBench instances."""
    if shuffle:
        instances = sorted(instances.copy(), key=lambda x: x["instance_id"])
        random.seed(42)
        random.shuffle(instances)
    before_filter = len(instances)
    instances = [instance for instance in instances if re.match(filter_spec, instance["instance_id"])]
    if (after_filter := len(instances)) != before_filter:
        logger.info(f"Instance filter: {before_filter} -> {after_filter} instances")
    if slice_spec:
        values = [int(x) if x else None for x in slice_spec.split(":")]
        instances = instances[slice(*values)]
        if (after_slice := len(instances)) != before_filter:
            logger.info(f"Instance slice: {before_filter} -> {after_slice} instances")
    return instances


# fmt: off
@app.command(help=_HELP_TEXT)
def main(
    subset: str = typer.Option("lite", "--subset", help="SWEBench subset to use or path to a dataset", rich_help_panel="Data selection"),
    split: str = typer.Option("dev", "--split", help="Dataset split", rich_help_panel="Data selection"),
    slice_spec: str = typer.Option("", "--slice", help="Slice specification (e.g., '0:5' for first 5 instances)", rich_help_panel="Data selection"),
    filter_spec: str = typer.Option("", "--filter", help="Filter instance IDs by regex", rich_help_panel="Data selection"),
    shuffle: bool = typer.Option(False, "--shuffle", help="Shuffle instances", rich_help_panel="Data selection"),
    output: str = typer.Option("", "-o", "--output", help="Output directory", rich_help_panel="Basic"),
    workers: int = typer.Option(1, "-w", "--workers", help="Number of worker threads for parallel processing", rich_help_panel="Basic"),
    model: str | None = typer.Option(None, "-m", "--model", help="Model to use", rich_help_panel="Basic"),
    model_class: str | None = typer.Option(None, "--model-class", help="Model class to use (e.g., 'anthropic' or 'minisweagent.models.anthropic.AnthropicModel')", rich_help_panel="Advanced"),
    redo_existing: bool = typer.Option(False, "--redo-existing", help="Redo existing instances", rich_help_panel="Data selection"),
    config_spec: list[str] = typer.Option([str(DEFAULT_CONFIG_FILE)], "-c", "--config", help=_CONFIG_SPEC_HELP_TEXT, rich_help_panel="Basic"),
    environment_class: str | None = typer.Option(None, "--environment-class", help="Environment type to use. Recommended are docker or singularity", rich_help_panel="Advanced"),
    build: bool = typer.Option(False, "--build", help="Attempt to build the docker image if it is not found locally", rich_help_panel="Advanced"),
) -> None:
    # fmt: on
    dotenv.load_dotenv()
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Results will be saved to {output_path}")
    add_file_handler(output_path / "minisweagent.log")

    from datasets import load_dataset

    dataset_path = DATASET_MAPPING.get(subset, subset)
    logger.info(f"Loading dataset {dataset_path}, split {split}...")
    instances = list(load_dataset(dataset_path, split=split))

    instances = filter_instances(instances, filter_spec=filter_spec, slice_spec=slice_spec, shuffle=shuffle)
    if not redo_existing and (output_path / "preds.json").exists():
        existing_instances = list(json.loads((output_path / "preds.json").read_text()).keys())
        logger.info(f"Skipping {len(existing_instances)} existing instances")
        instances = [instance for instance in instances if instance["instance_id"] not in existing_instances]
    logger.info(f"Running on {len(instances)} instances...")

    logger.info(f"Building agent config from specs: {config_spec}")
    configs = [get_config_from_spec(spec) for spec in config_spec]
    configs.append({
        "environment": {"environment_class": environment_class or UNSET},
        "model": {
            "model_name": model or os.getenv("MSWEA_MODEL_NAME") or UNSET,
            "model_class": model_class or UNSET,
        },
    })
    config = recursive_merge(*configs)

    progress_manager = RunBatchProgressManager(len(instances), output_path / f"exit_statuses_{time.time()}.yaml")

    def process_futures(futures: dict[concurrent.futures.Future, str]):
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except concurrent.futures.CancelledError:
                pass
            except Exception as e:
                instance_id = futures[future]
                logger.error(f"Error in future for instance {instance_id}: {e}", exc_info=True)
                progress_manager.on_uncaught_exception(instance_id, e)

    with Live(progress_manager.render_group, refresh_per_second=4):
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(process_instance, instance, output_path, config, progress_manager, build): instance[
                    "instance_id"
                ]
                for instance in instances
            }
            try:
                process_futures(futures)
            except KeyboardInterrupt:
                logger.info("Cancelling all pending jobs. Press ^C again to exit immediately.")
                for future in futures:
                    if not future.running() and not future.done():
                        future.cancel()
                process_futures(futures)


if __name__ == "__main__":
    app()
