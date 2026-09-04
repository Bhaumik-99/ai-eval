import requests, json

r = requests.get('http://localhost:8000/api/evaluations')
evals = r.json()
print(f'Total evaluations: {len(evals)}')
for e in evals:
    eid = e['id']
    model = e['agent_model']
    status = e['status']
    avg = e.get('avg_score', '?')
    runs = e.get('run_count', 0)
    passed = e.get('passed_count', 0)
    print(f"  [{eid}] model={model} status={status} avg={avg}% tasks={runs} passed={passed}")

# Get detail of first eval
if evals:
    eid = evals[0]['id']
    r2 = requests.get(f'http://localhost:8000/api/evaluations/{eid}')
    detail = r2.json()
    print(f"\nDetail for evaluation {eid}:")
    for tr in detail.get('task_runs', []):
        score = tr.get('final_score', 0)
        status = tr.get('status', '?')
        name = tr.get('task_name', tr.get('task_id'))
        hidden = tr.get('hidden_score', 0)
        reg = tr.get('regression_score', 0)
        cat = tr.get('failure_category', '-')
        passed_count = len([t for t in tr.get('failure_details', []) if t.get('passed')])
        total_count = len(tr.get('failure_details', []))
        print(f"  {status.upper():6s} {score:5.1f}%  {name:35s}  hidden={hidden:.0f}% reg={reg:.0f}% cat={cat}  tests={passed_count}/{total_count}")
