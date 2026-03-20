"""Quick single-case test runner for regression testing."""
import json, requests, time, sys

AGENT_URL = "http://127.0.0.1:8005/solve"
SANDBOX_URL = "https://kkpqfuj-amager.tripletex.dev/v2"
SESSION_TOKEN = "eyJ0b2tlbklkIjoyMTQ3NjI5MzM3LCJ0b2tlbiI6IjQ4MTIyOGIzLWU3MWUtNDBiOC1hOWJjLTVmY2I3YTc2YmI0NyJ9"

def run_case(case_file):
    case = json.load(open(case_file, encoding="utf-8"))
    print(f"TEST: {case_file}")
    print(f"Prompt: {case['prompt'][:150]}")
    t0 = time.time()
    try:
        r = requests.post(AGENT_URL, json={
            "prompt": case["prompt"],
            "files": case.get("files", []),
            "tripletex_credentials": {
                "base_url": SANDBOX_URL,
                "session_token": SESSION_TOKEN,
            },
        }, timeout=300)
        elapsed = time.time() - t0
        if r.status_code == 200:
            d = r.json()
            status = d.get("status", "?")
            iters = d.get("iterations", 0)
            calls = d.get("api_calls", [])
            errors = d.get("errors", [])
            tokens = d.get("tokens", 0)
            print(f"RESULT: {status} | {elapsed:.1f}s | iters={iters} | calls={len(calls)} | errors={len(errors)} | tokens={tokens}")
            for c in calls:
                marker = "x" if "-> 4" in c or "-> 5" in c else "v"
                print(f"  [{marker}] {c}")
            for e in errors:
                print(f"  ERR: {e[:200]}")
            return status == "completed"
        else:
            print(f"HTTP ERROR: {r.status_code} in {elapsed:.1f}s")
            print(r.text[:500])
            return False
    except requests.Timeout:
        print(f"TIMEOUT after {time.time()-t0:.1f}s")
        return False
    except Exception as ex:
        print(f"EXCEPTION: {ex}")
        return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        success = run_case(sys.argv[1])
        sys.exit(0 if success else 1)
    else:
        print("Usage: python run_one_test.py <case_file>")
