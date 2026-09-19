import json
import httpx
import argparse
import asyncio
import os
from pathlib import Path

def overlaps(l1_s, l1_e, l2_s, l2_e):
    return max(l1_s, l2_s) <= min(l1_e, l2_e)

async def run_evaluation(backend_url: str):
    eval_dir = Path(__file__).parent
    samples_dir = eval_dir / "test_samples"
    truth_file = samples_dir / "ground_truth.json"
    
    with open(truth_file, "r") as f:
        ground_truth = json.load(f)
        
    files_to_review = list(set(gt["file"] for gt in ground_truth))
    
    tp = 0
    fp = 0
    fn = 0
    
    severity_stats = {"critical": {"tp":0, "fp":0, "fn":0}, "high": {"tp":0, "fp":0, "fn":0}, "medium": {"tp":0, "fp":0, "fn":0}, "low": {"tp":0, "fp":0, "fn":0}}
    source_stats = {"semgrep": 0, "llm": 0, "osv": 0}
    
    all_findings = []
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        for filename in files_to_review:
            file_path = samples_dir / filename
            with open(file_path, "r") as f:
                code = f.read()
                
            req = {
                "file_path": str(file_path),
                "code": code,
                "commit_hash": "test"
            }
            try:
                resp = await client.post(f"{backend_url}/review", json=req)
                resp.raise_for_status()
                data = resp.json()
                findings = data.get("findings", [])
                for f in findings:
                    f["test_file"] = filename
                all_findings.extend(findings)
            except Exception as e:
                print(f"Error reviewing {filename}: {e}")
                
    # Match findings to ground truth
    matched_gt = set()
    matched_findings = set()
    
    for i, finding in enumerate(all_findings):
        f_file = finding["test_file"]
        f_start = finding.get("line_start", -1)
        f_end = finding.get("line_end", f_start)
        
        source = finding.get("source", {}).get("name", "unknown")
        if source in source_stats:
            source_stats[source] += 1
            
        matched = False
        for j, gt in enumerate(ground_truth):
            if j in matched_gt:
                continue
            if gt["file"] == f_file and overlaps(f_start, f_end, gt["line_start"], gt["line_end"]):
                matched = True
                matched_gt.add(j)
                matched_findings.add(i)
                tp += 1
                severity_stats[gt.get("severity", "medium")]["tp"] += 1
                break
                
        if not matched:
            fp += 1
            sev = finding.get("severity", "medium")
            if sev in severity_stats:
                severity_stats[sev]["fp"] += 1
                
    for j, gt in enumerate(ground_truth):
        if j not in matched_gt:
            fn += 1
            severity_stats[gt.get("severity", "medium")]["fn"] += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    print(f"## Security Evaluation Results")
    print(f"| Metric | Value |")
    print(f"|--------|-------|")
    print(f"| True Positives (TP) | {tp} |")
    print(f"| False Positives (FP) | {fp} |")
    print(f"| False Negatives (FN) | {fn} |")
    print(f"| Precision | {precision:.3f} |")
    print(f"| Recall | {recall:.3f} |")
    print(f"| F1 Score | {f1:.3f} |")
    
    results = {
        "metrics": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1
        },
        "severity_stats": severity_stats,
        "source_stats": source_stats
    }
    
    res_dir = eval_dir / "results"
    res_dir.mkdir(exist_ok=True)
    with open(res_dir / "security_metrics.json", "w") as f:
        json.dump(results, f, indent=2)
        
    print(f"\nResults written to {res_dir / 'security_metrics.json'}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="http://localhost:8000")
    args = parser.parse_args()
    asyncio.run(run_evaluation(args.backend))
