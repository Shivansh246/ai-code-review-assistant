"""
Generate a synthetic dataset for the multimodal audio-code fusion experiment.
"""

import json
import random
import os

def generate_dataset():
    random.seed(42)
    voice_commands = [
        "review this code", 
        "explain the vulnerability", 
        "show critical findings", 
        "fix this bug", 
        "accept this finding", 
        "reject this finding"
    ]
    
    code_snippets = [
        "def sql_query(user_id):\n    return f'SELECT * FROM users WHERE id = {user_id}'",
        "import os\n\ndef run_cmd(cmd):\n    os.system(cmd)",
        "def add(a, b):\n    return a + b",
        "class User:\n    def __init__(self, name):\n        self.name = name",
        "<h1>Hello World</h1>",
        "print('Starting background task...')",
    ]
    
    irrelevant_texts = [
        "The quick brown fox jumps over the lazy dog.",
        "Grocery list: milk, eggs, bread.",
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
        "Today's weather is sunny with a chance of rain.",
    ]
    
    dataset = []
    
    # 100 positive samples
    for _ in range(100):
        cmd = random.choice(voice_commands)
        code = random.choice(code_snippets)
        dataset.append({
            "audio": cmd,
            "code": code,
            "label": 1
        })
        
    # 100 negative samples
    for _ in range(100):
        cmd = random.choice(voice_commands)
        code = random.choice(irrelevant_texts)
        dataset.append({
            "audio": cmd,
            "code": code,
            "label": 0
        })
        
    # Shuffle dataset
    random.shuffle(dataset)
    
    # Save to JSON
    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, "dataset.json")
    
    with open(out_path, "w") as f:
        json.dump(dataset, f, indent=4)
        
    print(f"Generated 200 samples and saved to {out_path}")

if __name__ == "__main__":
    generate_dataset()
