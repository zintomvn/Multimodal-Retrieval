from __future__ import annotations

import argparse
import json
import urllib.request


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and export a submission from a JSON rows file.")
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--name", default="submission_cli")
    parser.add_argument("--rows-json", required=True, help="Path to JSON list of submission rows.")
    args = parser.parse_args()

    with open(args.rows_json, "r", encoding="utf-8") as handle:
        rows = json.load(handle)

    submission = post_json(f"{args.api_base}/api/submissions", {"dataset_id": args.dataset_id, "name": args.name})
    post_json(f"{args.api_base}/api/submissions/{submission['id']}/items", {"rows": rows})
    exported = post_json(f"{args.api_base}/api/submissions/{submission['id']}/export", {})
    print(json.dumps(exported, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
