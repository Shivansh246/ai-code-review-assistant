"""
Generate a synthetic dataset for the comparative multimodal experiment.

This dataset focuses on ambiguous developer voice utterances (e.g., "check this", "explain this",
"fix this", "what is wrong here?", "is this safe?", "patch this issue") paired with code context:
- No finding / clean or raw code -> intent is 'review' (scans/audits the code)
- Code snippet with vulnerability finding / explanation request -> intent is 'explain' (explains why it is dangerous/root cause)
- Code snippet with proposed fix / bug comment / patch request -> intent is 'generate_fix' (generates remediation/patch)

The experiment tests whether fusing code context with voice transcript improves intent
classification over voice transcript alone.
"""

import json
import os
import random
from typing import Any, Dict, List

SHARED_AMBIGUOUS_COMMANDS = [
    "check this",
    "can you check this",
    "look at this",
    "take care of this",
    "handle this issue",
    "what about this",
    "inspect this part",
    "see this code",
    "process this",
    "evaluate this",
    "what to do with this",
]

AMBIGUOUS_COMMAND_TEMPLATES = {
    "review": [
        "check this",
        "can you check this",
        "look at this",
        "take care of this",
        "handle this issue",
        "what about this",
        "inspect this part",
        "see this code",
        "process this",
        "evaluate this",
        "what to do with this",
        "audit this file",
        "scan this file",
        "run a check on this",
    ],
    "explain": [
        "check this",
        "can you check this",
        "look at this",
        "take care of this",
        "handle this issue",
        "what about this",
        "inspect this part",
        "see this code",
        "process this",
        "evaluate this",
        "what to do with this",
        "what is wrong here",
        "why is this flagged",
        "what does this warning mean",
    ],
    "generate_fix": [
        "check this",
        "can you check this",
        "look at this",
        "take care of this",
        "handle this issue",
        "what about this",
        "inspect this part",
        "see this code",
        "process this",
        "evaluate this",
        "what to do with this",
        "make this safe",
        "resolve this issue",
        "patch this problem",
    ],
}

CODE_CONTEXT_TEMPLATES = {
    "review": [
        "def fetch_user_data(user_id):\n    # Standard database lookup\n    query = 'SELECT id, username FROM users WHERE id = %s'\n    return db.execute(query, (user_id,))",
        "import math\n\ndef calculate_hypotenuse(a, b):\n    return math.sqrt(a**2 + b**2)",
        "class ConfigManager:\n    def __init__(self, path):\n        self.path = path\n    def load(self):\n        with open(self.path, 'r') as f:\n            return json.load(f)",
        "async def handle_request(request):\n    data = await request.json()\n    return {'status': 'ok', 'received': data}",
        "def sanitize_filename(filename):\n    return os.path.basename(filename.strip())",
    ],
    "explain": [
        "# Finding ID: f-sqli-01\n# Rule: sql-injection-vulnerability\n# Description: Untrusted user input is directly concatenated into SQL query string\nquery = f'SELECT * FROM accounts WHERE user = \"{user_input}\"'",
        "# Finding ID: f-cmd-02\n# Rule: command-injection\n# Description: os.system executes unsanitized command parameter from network request\ndef run_backup(path):\n    os.system(f'tar -czf backup.tar.gz {path}')",
        "# Finding ID: f-cred-03\n# Rule: hardcoded-api-key\n# Description: High entropy string detected matching AWS secret access key pattern\nAWS_SECRET_KEY = 'AKIAIOSFODNN7EXAMPLE'",
        "# Finding ID: f-path-04\n# Rule: path-traversal\n# Description: Arbitrary file read via relative path traversal without bounds checking\nwith open(os.path.join(base_dir, user_supplied_path)) as f:\n    return f.read()",
        "# Finding ID: f-proto-05\n# Rule: prototype-pollution\n# Description: Recursive object merge does not protect __proto__ property",
    ],
    "generate_fix": [
        "# Target: Remediation needed\n# Issue: SQL injection in search_query\n# Fix plan: Replace f-string with parameterized query\nquery = f'SELECT * FROM items WHERE name LIKE \"%{term}%\"'",
        "# Target: Remediation needed\n# Issue: Command injection in subprocess.Popen shell=True\n# Fix plan: Pass arguments as list with shell=False\nsubprocess.Popen(f'ping -c 1 {host}', shell=True)",
        "# Target: Remediation needed\n# Issue: Insecure deserialization using pickle.loads\n# Fix plan: Migrate to json.loads or safe deserializer\ndata = pickle.loads(raw_cookie)",
        "# Target: Remediation needed\n# Issue: Insecure temporary file creation\n# Fix plan: Use tempfile.NamedTemporaryFile with delete=True\ntemp_path = '/tmp/temp_report_' + str(random.randint(1, 1000))",
        "# Target: Remediation needed\n# Issue: Unhandled exception in TLS certificate verification\n# Fix plan: Enforce verify=True in requests.get\nrequests.get(target_url, verify=False)",
    ],
}


def generate_multimodal_dataset(samples_per_class: int = 100, seed: int = 42) -> List[Dict[str, Any]]:
    """
    Generate a balanced dataset of ambiguous voice commands and accompanying code context.
    Total samples = len(classes) * samples_per_class (default 3 * 100 = 300).
    """
    rng = random.Random(seed)
    dataset: List[Dict[str, Any]] = []

    classes = ["review", "explain", "generate_fix"]

    for label in classes:
        commands = AMBIGUOUS_COMMAND_TEMPLATES[label]
        contexts = CODE_CONTEXT_TEMPLATES[label]

        for _ in range(samples_per_class):
            cmd = rng.choice(commands)
            code = rng.choice(contexts)
            dataset.append({
                "audio": cmd,
                "code": code,
                "label": label,
            })

    rng.shuffle(dataset)
    return dataset


def save_dataset(dataset: List[Dict[str, Any]], out_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2)


if __name__ == "__main__":
    exp_dir = os.path.dirname(os.path.abspath(__file__))
    out_file = os.path.join(exp_dir, "dataset.json")
    data = generate_multimodal_dataset(samples_per_class=100, seed=42)
    save_dataset(data, out_file)
    print(f"Generated {len(data)} balanced samples and saved to {out_file}")
