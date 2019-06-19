import os
import argparse
import json
from jinja2 import Environment, FileSystemLoader


BASEDIR = os.path.dirname(__file__)
env = Environment(loader=FileSystemLoader(f"{BASEDIR}/bindings_templates"))


GD_TYPES = {"enum.Error": "godot_error", "enum.Vector3::Axis": "godot_vector3_axis"}


def cook_godot_type(raw_type):
    if not raw_type.startswith("enum."):
        return raw_type
    else:
        try:
            return GD_TYPES[raw_type]
        except KeyError:
            return "int"


def generate_bindings(output_path, input_path):
    with open(input_path, "r") as fd:
        data = json.load(fd)
    template = env.get_template("bindings.tmpl.pyx")
    out = template.render(data=data, cook_godot_type=cook_godot_type)
    with open(output_path, "w") as fd:
        fd.write(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate godot api bindings file")
    parser.add_argument(
        "--input", "-i", help="Path to Godot api.json file", default="api.json"
    )
    parser.add_argument("--output", "-o", default="godot_bindings_gen.pyx")
    args = parser.parse_args()
    generate_bindings(args.output, args.input)
