from agent import generate_brd_json
import time

TEST_CASES = [
    {
        "id": "clear-01",
        "request": "We need customers to reset their own password instead of calling support. Support gets ~400 tickets/month for this.",
        "expect_min_functional_requirements": 3,
        "expect_open_questions": False,  # request is specific enough, shouldn't need many
    },
    {
        "id": "clear-02",
        "request": "Build an internal tool for finance to approve vendor invoices over $5,000.",
        "expect_min_functional_requirements": 2,
        "expect_open_questions": False,
    },
    {
        "id": "vague-01",
        "request": "I want to build an AI startup.",
        "expect_min_functional_requirements": 0,
        "expect_open_questions": True,  # too vague — MUST ask, not guess
    },
    {
        "id": "vague-02",
        "request": "Make things better for our users.",
        "expect_min_functional_requirements": 0,
        "expect_open_questions": True,
    },
]


def check_result(case, data):
    """Returns a list of failure reasons. Empty list = passed."""
    failures = []

    required_keys = [
        "title", "executive_summary", "business_objectives",
        "in_scope", "out_of_scope", "functional_requirements",
        "assumptions", "open_questions"
    ]
    for key in required_keys:
        if key not in data or not data[key]:
            failures.append(f"missing or empty field: {key}")

    fr_count = len(data.get("functional_requirements", []))
    if fr_count < case["expect_min_functional_requirements"]:
        failures.append(
            f"expected >= {case['expect_min_functional_requirements']} functional requirements, got {fr_count}"
        )

    has_open_questions = len(data.get("open_questions", [])) > 0
    if case["expect_open_questions"] and not has_open_questions:
        failures.append("expected open questions for a vague request, got none — model may be hallucinating specifics instead of asking")

    return failures


def run_eval():
    results = []
    for case in TEST_CASES:
        start = time.time()
        try:
            data = generate_brd_json(case["request"])
            elapsed = round(time.time() - start, 2)
            failures = check_result(case, data)
            passed = len(failures) == 0
        except Exception as e:
            elapsed = round(time.time() - start, 2)
            failures = [f"generation crashed: {e}"]
            passed = False

        results.append({"id": case["id"], "passed": passed, "failures": failures, "time": elapsed})
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {case['id']} ({elapsed}s)")
        for f in failures:
            print(f"    - {f}")

    passed_count = sum(1 for r in results if r["passed"])
    print(f"\n{passed_count}/{len(results)} passed")
    return results


if __name__ == "__main__":
    run_eval()
    