from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from google.cloud.firestore import Client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.gmo_bot.infra.config.firestore_config_repo import MODELS_COLLECTION_ID

ALLOWED_DIRECTIONS = ("LONG", "SHORT", "BOTH")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Update the `direction` field of "
            f"{MODELS_COLLECTION_ID}/<model_id>/config/current in Firestore. "
            "Supports hot-reload; no restart required."
        )
    )
    parser.add_argument("--model-id", required=True, help="Firestore model document id")
    parser.add_argument(
        "--direction",
        required=True,
        choices=ALLOWED_DIRECTIONS,
        help="New direction value to write",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation prompt",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is required")

    firestore = Client.from_service_account_json(credentials_path)
    model_ref = firestore.collection(MODELS_COLLECTION_ID).document(args.model_id)
    config_ref = model_ref.collection("config").document("current")

    model_snapshot = model_ref.get()
    if not model_snapshot.exists:
        raise RuntimeError(f"{MODELS_COLLECTION_ID}/{args.model_id} does not exist")
    config_snapshot = config_ref.get()
    if not config_snapshot.exists:
        raise RuntimeError(
            f"{MODELS_COLLECTION_ID}/{args.model_id}/config/current does not exist"
        )

    current_model_direction = (model_snapshot.to_dict() or {}).get("direction")
    current_config_direction = (config_snapshot.to_dict() or {}).get("direction")

    print(
        f"[update] {MODELS_COLLECTION_ID}/{args.model_id}.direction: "
        f"{current_model_direction!r} -> {args.direction!r}"
    )
    print(
        f"[update] {MODELS_COLLECTION_ID}/{args.model_id}/config/current.direction: "
        f"{current_config_direction!r} -> {args.direction!r}"
    )
    if current_model_direction == args.direction and current_config_direction == args.direction:
        print("[update] no change needed; exiting")
        return

    if not args.yes:
        answer = input("proceed? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("[update] aborted")
            return

    batch = firestore.batch()
    batch.update(model_ref, {"direction": args.direction})
    batch.update(config_ref, {"direction": args.direction})
    batch.commit()
    print("[update] done")


if __name__ == "__main__":
    main()
