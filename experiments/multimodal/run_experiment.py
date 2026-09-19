"""
Run the multimodal experiment: load dataset, train, evaluate, and save results.
"""

import json
import os
import subprocess
from audio_code_fusion import AudioCodeFusionClassifier, evaluate_model

def main():
    exp_dir = os.path.dirname(os.path.abspath(__file__))
    dataset_path = os.path.join(exp_dir, "dataset.json")
    results_path = os.path.join(exp_dir, "results.json")
    
    # Generate if not exists
    if not os.path.exists(dataset_path):
        print("Dataset not found. Generating...")
        import generate_dataset
        generate_dataset.generate_dataset()
        
    with open(dataset_path, "r") as f:
        dataset = json.load(f)
        
    # Split 80/20
    split_idx = int(len(dataset) * 0.8)
    train_data = dataset[:split_idx]
    test_data = dataset[split_idx:]
    
    X_train_audio = [d["audio"] for d in train_data]
    X_train_code = [d["code"] for d in train_data]
    y_train = [d["label"] for d in train_data]
    
    X_test_audio = [d["audio"] for d in test_data]
    X_test_code = [d["code"] for d in test_data]
    y_test = [d["label"] for d in test_data]
    
    print(f"Training on {len(train_data)} samples...")
    classifier = AudioCodeFusionClassifier()
    classifier.fit(X_train_audio, X_train_code, y_train)
    
    print(f"Evaluating on {len(test_data)} samples...")
    y_pred = classifier.predict(X_test_audio, X_test_code)
    
    metrics = evaluate_model(y_test, y_pred.tolist())
    
    print("\n--- Evaluation Metrics ---")
    for k, v in metrics.items():
        print(f"{k}: {v}")
        
    with open(results_path, "w") as f:
        json.dump(metrics, f, indent=4)
        
    print(f"\nResults saved to {results_path}")

if __name__ == "__main__":
    main()
