"""Post-acceptance UCL image retention; dry-run unless --apply is supplied.

Run on the Docker host with `uv run python scripts/prune_release_images.py`.
Never run concurrently with a build, rollout, or rollback.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess


RELEASE_TAG = re.compile(r"ucl-solkoff:\d{8}T\d{6}Z-[0-9a-f]{7,40}-[0-9a-f]{12,64}\Z")
CONTAINER = "ucl-solkoff-ucl-solkoff-1"


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def removal_plan(metadata, images, containers):
    """Fail closed on stale metadata; remove only unused immutable UCL tags."""
    if metadata.get("phase") not in {
        "production_verified", "production_verified_with_limitations"
    }:
        raise ValueError("Release acceptance is not complete")
    active = metadata["candidate_image_id"]
    rollback = metadata["rollback_image_id"]
    by_id = {image["Id"]: image for image in images}
    if active not in by_id or rollback not in by_id:
        raise ValueError("Active or rollback image missing; cleanup blocked")
    live = [c for c in containers if "/" + CONTAINER in c["Names"]]
    if len(live) != 1 or live[0]["ImageID"] != active or live[0]["State"] != "running":
        raise ValueError("Running image does not match active-release metadata")
    for key, image_id in (("candidate_image", active), ("rollback_image", rollback)):
        if metadata[key] not in (by_id[image_id].get("RepoTags") or []):
            raise ValueError(f"Pinned {key} tag no longer matches its image ID")
    protected = {active, rollback} | {c["ImageID"] for c in containers}
    return sorted({
        tag
        for image in images if image["Id"] not in protected
        for tag in image.get("RepoTags") or []
        if RELEASE_TAG.fullmatch(tag)
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path,
                        default=Path("/opt/ucl-solkoff/active-release.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text())
    image_ids = docker("image", "ls", "--quiet", "--no-trunc").split()
    images = json.loads(docker("image", "inspect", *sorted(set(image_ids)))) if image_ids else []
    container_ids = docker("ps", "--all", "--quiet").split()
    # Container inspect includes secrets, so request only the fields needed here.
    containers = []
    for container_id in container_ids:
        info = docker("inspect", "--format",
                      '{{.Image}} {{.State.Status}} {{.Name}}', container_id).split()
        containers.append({"ImageID": info[0], "State": info[1], "Names": [info[2]]})
    plan = removal_plan(metadata, images, containers)
    print(json.dumps({"apply": args.apply, "remove_tags": plan}, indent=2))
    for tag in plan:
        if args.apply:
            # Never force image deletion, remove containers, or prune shared state.
            docker("image", "rm", tag)


if __name__ == "__main__":
    main()
