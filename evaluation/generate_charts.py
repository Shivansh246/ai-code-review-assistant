"""
generate_charts.py
Generates PNG charts from evaluation/results/ JSON files.
"""
import json, os, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, 'evaluation', 'results')
CHARTS  = os.path.join(RESULTS, 'charts')
os.makedirs(CHARTS, exist_ok=True)

# 1. Security detection metrics
with open(os.path.join(RESULTS, 'security_metrics.json')) as f:
    sec = json.load(f)
m = sec['metrics']
fig, axes = plt.subplots(1, 2, figsize=(12, 5))
fig.suptitle('Security Vulnerability Detection (Semgrep + OSV vs. Ground Truth)\nN=14 ground-truth findings across 2 files', fontsize=11)
ax = axes[0]
bars = ax.bar(['TP', 'FP', 'FN'], [m['tp'], m['fp'], m['fn']], color=['#2ecc71','#e74c3c','#f39c12'], edgecolor='black')
ax.set_title('Finding Counts'); ax.set_ylabel('Count')
for bar, val in zip(bars, [m['tp'], m['fp'], m['fn']]):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3, str(val), ha='center', fontweight='bold')
ax = axes[1]
metrics_d = {'Precision': m['precision'], 'Recall': m['recall'], 'F1': m['f1']}
bars = ax.bar(list(metrics_d.keys()), list(metrics_d.values()), color=['#3498db','#9b59b6','#1abc9c'], edgecolor='black')
ax.set_ylim(0, 1.1); ax.set_title('Precision / Recall / F1'); ax.set_ylabel('Score')
for bar, val in zip(bars, metrics_d.values()):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02, f'{val:.3f}', ha='center', fontweight='bold')
plt.tight_layout()
plt.savefig(os.path.join(CHARTS, 'security_detection_metrics.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Saved security_detection_metrics.png")

# 2. Severity breakdown
sev_stats = sec['severity_stats']
severities = ['critical', 'high', 'medium']
tp_vals = [sev_stats[s]['tp'] for s in severities]
fp_vals = [sev_stats[s]['fp'] for s in severities]
fn_vals = [sev_stats[s]['fn'] for s in severities]
x = np.arange(len(severities)); w = 0.25
fig, ax = plt.subplots(figsize=(9, 5))
ax.bar(x - w, tp_vals, w, label='TP', color='#2ecc71', edgecolor='black')
ax.bar(x,     fp_vals, w, label='FP', color='#e74c3c', edgecolor='black')
ax.bar(x + w, fn_vals, w, label='FN', color='#f39c12', edgecolor='black')
ax.set_xticks(x); ax.set_xticklabels([s.capitalize() for s in severities])
ax.set_title('Detection Results by Severity'); ax.set_ylabel('Count'); ax.legend()
plt.tight_layout()
plt.savefig(os.path.join(CHARTS, 'security_by_severity.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Saved security_by_severity.png")

# 3. Prioritisation comparison
with open(os.path.join(RESULTS, 'prioritisation_metrics.json')) as f:
    pri = json.load(f)
ev = pri['evaluation']
method_keys = ['static_metrics', 'adaptive_metrics', 'rl_cold_metrics', 'rl_trained_metrics']
labels  = ['Static\nBaseline', 'Adaptive\n(Phase 6A)', 'RL\nCold-Start', 'RL\nTrained']
prec   = [ev[m]['precision'] for m in method_keys]
recall = [ev[m]['recall']    for m in method_keys]
f1s    = [ev[m]['f1']        for m in method_keys]
top3fp = [ev[m]['fp']        for m in method_keys]

fig, axes = plt.subplots(1, 2, figsize=(13, 5))
fig.suptitle('Finding Prioritisation: Top-3 Detection Metrics (Synthetic Feedback Fixture, Seed=42)', fontsize=10)
x = np.arange(len(labels)); w = 0.25
ax = axes[0]
ax.bar(x - w, prec, w, label='Precision', color='#3498db', edgecolor='black')
ax.bar(x,     recall, w, label='Recall',  color='#9b59b6', edgecolor='black')
ax.bar(x + w, f1s,   w, label='F1',      color='#1abc9c', edgecolor='black')
ax.set_xticks(x); ax.set_xticklabels(labels); ax.set_ylim(0, 1.15)
ax.set_title('Top-3 Precision / Recall / F1'); ax.legend()
for i, (p, r, f) in enumerate(zip(prec, recall, f1s)):
    ax.text(i-w, p+0.02, f'{p:.2f}', ha='center', fontsize=8)
    ax.text(i,   r+0.02, f'{r:.2f}', ha='center', fontsize=8)
    ax.text(i+w, f+0.02, f'{f:.2f}', ha='center', fontsize=8)
ax = axes[1]
colors_bar = ['#e74c3c' if v > 0 else '#2ecc71' for v in top3fp]
ax.bar(labels, top3fp, color=colors_bar, edgecolor='black')
ax.set_title('Top-3 False Positive Count'); ax.set_ylabel('FP Count')
for i, v in enumerate(top3fp):
    ax.text(i, v + 0.05, str(v), ha='center', fontweight='bold')
plt.tight_layout()
plt.savefig(os.path.join(CHARTS, 'prioritisation_comparison.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Saved prioritisation_comparison.png")

# 4. Multimodal intent
with open(os.path.join(RESULTS, 'intent_metrics.json')) as f:
    intent = json.load(f)
baseline = intent['baseline_transcript_only']
fusion   = intent['approach_transcript_code_fusion']
classes  = intent['metadata']['classes']
metrics_list = ['accuracy', 'f1_macro', 'precision_macro', 'recall_macro']
metric_labels = ['Accuracy', 'F1 (Macro)', 'Precision\n(Macro)', 'Recall\n(Macro)']
base_vals  = [baseline[m] for m in metrics_list]
fusion_vals = [fusion[m]  for m in metrics_list]
fig, axes = plt.subplots(1, 2, figsize=(13, 5))
fig.suptitle('Multimodal Intent Classification (Synthetic Dataset, N=300, Seed=42)', fontsize=10)
x = np.arange(len(metric_labels)); w = 0.35
ax = axes[0]
ax.bar(x - w/2, base_vals,  w, label='Baseline A\n(Transcript Only)', color='#e67e22', edgecolor='black')
ax.bar(x + w/2, fusion_vals, w, label='Approach B\n(+ Code Context)', color='#2980b9', edgecolor='black')
ax.set_xticks(x); ax.set_xticklabels(metric_labels); ax.set_ylim(0, 1.15)
ax.set_title('Overall Metrics'); ax.legend()
for i, (b, fu) in enumerate(zip(base_vals, fusion_vals)):
    ax.text(i - w/2, b + 0.02, f'{b:.3f}', ha='center', fontsize=8)
    ax.text(i + w/2, fu + 0.02, f'{fu:.3f}', ha='center', fontsize=8)
ax = axes[1]
base_f1  = [baseline['per_class'][c]['f1'] for c in classes]
fusion_f1 = [fusion['per_class'][c]['f1']  for c in classes]
x = np.arange(len(classes))
ax.bar(x - w/2, base_f1,  w, label='Baseline A', color='#e67e22', edgecolor='black')
ax.bar(x + w/2, fusion_f1, w, label='Approach B', color='#2980b9', edgecolor='black')
ax.set_xticks(x); ax.set_xticklabels(classes); ax.set_ylim(0, 1.15)
ax.set_title('Per-Class F1 Score'); ax.legend()
plt.tight_layout()
plt.savefig(os.path.join(CHARTS, 'intent_classification.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Saved intent_classification.png")

# 5. Performance latency
with open(os.path.join(RESULTS, 'performance_metrics.json')) as f:
    perf = json.load(f)
fig, axes = plt.subplots(1, 2, figsize=(12, 5))
fig.suptitle('Performance Measurements (Intel i5-1135G7, 8 GB RAM, Arch Linux)', fontsize=10)
rv = perf['review_single_file_s']
ax = axes[0]
ax.bar(['Min', 'Mean', 'Max'], [rv['min'], rv['mean'], rv['max']],
       color=['#27ae60','#2980b9','#c0392b'], edgecolor='black')
ax.set_title(f'/review Latency (n={rv["runs"]})\nSemgrep + OSV; LLM disabled'); ax.set_ylabel('Seconds')
for i, v in enumerate([rv['min'], rv['mean'], rv['max']]):
    ax.text(i, v + 0.05, f'{v:.3f}s', ha='center', fontweight='bold')
hp = perf['health_ping_ms']
ax = axes[1]
ax.bar(['Min', 'Mean', 'Max'], [hp['min'], hp['mean'], hp['max']],
       color=['#27ae60','#2980b9','#c0392b'], edgecolor='black')
ax.set_title(f'/health Ping Latency (n={hp["runs"]})'); ax.set_ylabel('Milliseconds')
for i, v in enumerate([hp['min'], hp['mean'], hp['max']]):
    ax.text(i, v + 0.1, f'{v:.2f}ms', ha='center', fontweight='bold')
plt.tight_layout()
plt.savefig(os.path.join(CHARTS, 'performance_latency.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Saved performance_latency.png")
print("\nAll charts saved to", CHARTS)
