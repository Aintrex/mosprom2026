"""Public packaged release metadata; plugin.json is the version source."""
import json
import os


with open(os.path.join(os.path.dirname(__file__), "plugin.json"), "r", encoding="utf-8") as stream:
    _metadata = json.load(stream)

PLUGIN_VERSION = _metadata["version"]
RELEASE_ID = _metadata["release_id"]
NUMERIC_ARGUMENT_POLICY = _metadata["numeric_argument_policy"]


def public_release():
    return {"version": PLUGIN_VERSION, "release_id": RELEASE_ID,
            "numeric_argument_policy": NUMERIC_ARGUMENT_POLICY}
