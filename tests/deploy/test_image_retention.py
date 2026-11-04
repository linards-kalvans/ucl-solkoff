import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "prune_release_images",
    Path(__file__).resolve().parents[2] / "scripts" / "prune_release_images.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
removal_plan = module.removal_plan


def fixture():
    tags = [f"ucl-solkoff:202610{day}T100000Z-0105334-123456789abc"
            for day in (12, 15, 26)]
    images = [{"Id": str(i), "RepoTags": [tag]} for i, tag in enumerate(tags)]
    metadata = {
        "phase": "production_verified", "candidate_image_id": "2",
        "rollback_image_id": "1", "candidate_image": tags[2],
        "rollback_image": tags[1],
    }
    containers = [{"ImageID": "2", "State": "running",
                   "Names": ["/ucl-solkoff-ucl-solkoff-1"]}]
    return metadata, images, containers


def test_retains_active_and_rollback_and_removes_only_old_ucl_tags():
    metadata, images, containers = fixture()
    images += [{"Id": "other", "RepoTags": ["other:latest", "ucl-solkoff:latest"]},
               {"Id": "dangling", "RepoTags": None}]
    assert removal_plan(metadata, images, containers) == images[0]["RepoTags"]


def test_retains_images_used_by_stopped_containers():
    metadata, images, containers = fixture()
    containers.append({"ImageID": "0", "State": "exited", "Names": ["/smoke"]})
    assert removal_plan(metadata, images, containers) == []


@pytest.mark.parametrize("fault", ["missing", "drift", "tag", "unverified", "stopped"])
def test_blocks_cleanup_on_untrusted_release_state(fault):
    metadata, images, containers = fixture()
    if fault == "missing":
        images.pop(1)
    elif fault == "drift":
        containers[0]["ImageID"] = "0"
    elif fault == "tag":
        metadata["rollback_image"] = "ucl-solkoff:wrong"
    elif fault == "unverified":
        metadata["phase"] = "cutover"
    else:
        containers[0]["State"] = "exited"
    with pytest.raises(ValueError):
        removal_plan(metadata, images, containers)
