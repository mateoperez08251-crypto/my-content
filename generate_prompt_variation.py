# -*- coding: utf-8 -*-
"""generate_prompt_variation.py

Utility script to generate diverse *frutinovela* / animal‑telenovela prompts
using a pool of variables (styles_pool.json) and a LLM.

The script works in three steps:
1️⃣ Load the JSON pool that contains lists for ``personajes``, ``acciones``,
   ``escenarios``, ``estilos_visuales`` and ``cámaras``.
2️⃣ Build a prompt template with placeholders and fill each placeholder with a
   random entry from the corresponding list.
3️⃣ Call the chosen LLM (default: ``gpt-oss-120b`` via ``groq`` CLI) passing the
   generated prompt.  Hyper‑parameters such as ``temperature`` and ``top_p`` are
   configurable from the command line.

The generated prompt looks like::

    <personaje> <acción> en <escenario> con estilo <estilo_visual> y cámara <cámara>

Example output::

    una manzana descubriendo una traición en una mansión lujosa con estilo
    Unreal Engine cyber‑noir y cámara crash‑zoom

The LLM response will be a short description of the scene (you can feed it to
your fine‑tuned Director model later).

Running the script
-------------------
```bash
python generate_prompt_variation.py \
    --model gpt-oss-120b \
    --temperature 0.9 \
    --top-p 0.95 \
    --seed 42   # optional, reproducible randomness
```

If you use a different inference backend replace the ``subprocess`` call inside
``run_model`` accordingly.
"""
import json
import random
import argparse
import subprocess
import os
import sys

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_pool(pool_path: str) -> dict:
    """Load the JSON pool file.

    Parameters
    ----------
    pool_path: str
        Absolute path to ``styles_pool.json``.
    """
    with open(pool_path, "r", encoding="utf-8") as f:
        return json.load(f)


def random_choice(pool: dict) -> dict:
    """Pick one random element from each category.

    Returns a dict with keys matching the placeholders used in the template.
    """
    return {
        "personaje": random.choice(pool["personajes"]),
        "acción": random.choice(pool["acciones"]),
        "escenario": random.choice(pool["escenarios"]),
        "estilo_visual": random.choice(pool["estilos_visuales"]),
        "cámara": random.choice(pool["cámaras"]),
    }


def build_prompt(variables: dict) -> str:
    """Create the final prompt string using the variables dict."""
    template = (
        "{personaje} {acción} en {escenario} con estilo {estilo_visual} "
        "y cámara {cámara}"
    )
    return template.format(**variables)


def run_model(prompt: str, model: str, temperature: float, top_p: float) -> str:
    """Execute the model via CLI.

    This implementation uses the ``groq`` CLI (a lightweight wrapper around the
    OpenAI‑compatible API).  Replace the command list with your own inference
    command if you run the model locally.
    """
    cmd = [
        "groq", "run",
        "--model", model,
        "--temperature", str(temperature),
        "--top-p", str(top_p),
        "--prompt", prompt,
    ]
    try:
        result = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return result.strip()
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"Model call failed: {e.output}\n")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Generate diverse frutinovela prompts.")
    parser.add_argument(
        "--pool",
        default=r"d:\my content\styles_pool.json",
        help="Path to the JSON pool file.",
    )
    parser.add_argument(
        "--model",
        default="gpt-oss-120b",
        help="Model identifier for the CLI backend.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.9,
        help="Sampling temperature (higher = more diverse).",
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=0.95,
        help="Nucleus sampling probability.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for reproducibility.",
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    pool = load_pool(args.pool)
    variables = random_choice(pool)
    prompt = build_prompt(variables)
    print("🔹 Generated prompt:\n", prompt)
    print("\n--- Model response ---")
    response = run_model(prompt, args.model, args.temperature, args.top_p)
    print(response)


if __name__ == "__main__":
    main()

# End of file
