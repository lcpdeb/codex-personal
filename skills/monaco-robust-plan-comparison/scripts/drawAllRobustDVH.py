import argparse
import csv
from pathlib import Path
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


DATA_LINE = re.compile(r"^(?P<structure>.*?)\s+(?P<dose>-?\d+(?:\.\d+)?)\s+(?P<volume>-?\d+(?:\.\d+)?)\s*$")
HEADER_LINE = re.compile(r"^Patient ID:\s*(?P<patient_id>.*?)\s*\|\s*Plan Name:\s*(?P<plan_name>.*?)\s*\|")
STRUCTURE_NAME_ALIASES = {
    "Body": "Body(Unsp.Tiss.)",
    "Outer Contour": "Outer Contour(Unsp.Tiss.)",
}


def read_plan_suffixes(manifest_path):
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest does not exist: {manifest_path}")
    comparisons = {}
    with manifest_path.open(encoding="utf-8-sig", newline="") as manifest_file:
        reader = csv.DictReader(manifest_file, delimiter="\t")
        required_columns = {"comparison_id", "selected", "include_in_plot", "plan_suffix"}
        missing_columns = required_columns.difference(reader.fieldnames or ())
        if missing_columns:
            raise ValueError(f"{manifest_path}: missing columns: {', '.join(sorted(missing_columns))}")
        for line_number, row in enumerate(reader, start=2):
            comparison_id = (row.get("comparison_id") or "").strip()
            if not comparison_id:
                raise ValueError(f"{manifest_path}:{line_number}: comparison_id is empty")
            selected = (row.get("selected") or "").strip()
            include_in_plot = (row.get("include_in_plot") or "").strip()
            if selected not in ("0", "1") or include_in_plot not in ("0", "1"):
                raise ValueError(f"{manifest_path}:{line_number}: selected and include_in_plot must be 0 or 1")
            if include_in_plot == "1" and selected != "1":
                raise ValueError(f"{manifest_path}:{line_number}: include_in_plot requires selected=1")
            plan_suffix = (row.get("plan_suffix") or "").strip()
            if selected == "1" and (not plan_suffix or Path(plan_suffix).name != plan_suffix):
                raise ValueError(f"{manifest_path}:{line_number}: invalid plan_suffix {plan_suffix!r}")
            state = (selected, include_in_plot, plan_suffix)
            if comparison_id in comparisons and comparisons[comparison_id] != state:
                raise ValueError(f"{manifest_path}:{line_number}: inconsistent fields for {comparison_id}")
            comparisons[comparison_id] = state
    plan_suffixes = []
    for selected, include_in_plot, plan_suffix in comparisons.values():
        if selected == "1" and include_in_plot == "1" and plan_suffix not in plan_suffixes:
            plan_suffixes.append(plan_suffix)
    if not plan_suffixes:
        raise ValueError(f"{manifest_path}: no user-confirmed rows have include_in_plot=1")
    return tuple(plan_suffixes)


def collect_input_paths(input_directory, manifest_path, plan_prefix):
    input_directory = Path(input_directory)
    manifest_path = Path(manifest_path)
    if not input_directory.is_dir():
        raise FileNotFoundError(f"DVH directory does not exist: {input_directory}")
    if not plan_prefix or Path(plan_prefix).name != plan_prefix:
        raise ValueError(f"invalid Plan prefix: {plan_prefix!r}")
    input_paths = []
    for plan_suffix in read_plan_suffixes(manifest_path):
        expected_plan_name = f"{plan_prefix}{plan_suffix}"
        suffix = f"_{expected_plan_name}_DVH_1.txt"
        matches = [path for path in input_directory.iterdir() if path.is_file() and path.name.endswith(suffix)]
        if len(matches) != 1:
            raise ValueError(f"{input_directory}: expected one DVH file ending with {suffix!r}, found {len(matches)}")
        patient_id = matches[0].name[:-len(suffix)]
        with matches[0].open(encoding="utf-8") as input_file:
            header_match = HEADER_LINE.match(input_file.readline().strip())
        if header_match is None:
            raise ValueError(f"{matches[0]}: invalid DVH identity header")
        if header_match.group("patient_id") != patient_id or header_match.group("plan_name") != expected_plan_name:
            raise ValueError(f"{matches[0]}: filename and DVH identity header do not match")
        input_paths.append(matches[0])
    return tuple(input_paths)


def collect_plan_path(input_directory, plan_name):
    input_directory = Path(input_directory)
    if not plan_name or Path(plan_name).name != plan_name:
        raise ValueError(f"invalid Plan name: {plan_name!r}")
    suffix = f"_{plan_name}_DVH_1.txt"
    matches = [path for path in input_directory.iterdir() if path.is_file() and path.name.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"{input_directory}: expected one DVH file ending with {suffix!r}, found {len(matches)}")
    patient_id = matches[0].name[:-len(suffix)]
    with matches[0].open(encoding="utf-8") as input_file:
        header_match = HEADER_LINE.match(input_file.readline().strip())
    if header_match is None:
        raise ValueError(f"{matches[0]}: invalid DVH identity header")
    if header_match.group("patient_id") != patient_id or header_match.group("plan_name") != plan_name:
        raise ValueError(f"{matches[0]}: filename and DVH identity header do not match")
    return matches[0]


def load_curves(input_path):
    curves = {}
    bInData = False
    with input_path.open(encoding="utf-8") as input_file:
        for line_number, raw_line in enumerate(input_file, start=1):
            stripped_line = raw_line.strip()
            if stripped_line.startswith("Structure Name"):
                bInData = True
                continue
            if not bInData:
                continue
            if not stripped_line:
                if curves:
                    break
                continue

            match = DATA_LINE.match(raw_line)
            if match is None:
                raise ValueError(f"{input_path}:{line_number}: invalid DVH row")
            structure_name = match.group("structure").strip()
            dose = float(match.group("dose"))
            volume = float(match.group("volume"))
            if not structure_name or not np.isfinite(dose) or not np.isfinite(volume):
                raise ValueError(f"{input_path}:{line_number}: invalid DVH value")
            if structure_name not in curves:
                curves[structure_name] = ([], [])
            doses, volumes = curves[structure_name]
            doses.append(dose)
            volumes.append(volume)

    if not curves:
        raise ValueError(f"{input_path}: no DVH data rows")
    return {structure_name: (np.asarray(doses), np.asarray(volumes)) for structure_name, (doses, volumes) in curves.items()}


def read_constraint_structures(plan_directory):
    plan_directory = Path(plan_directory)
    hyp_paths = tuple(plan_directory.glob("*.hyp"))
    if len(hyp_paths) != 1:
        raise ValueError(f"{plan_directory}: expected one IMRT constraint file (*.hyp), found {len(hyp_paths)}")
    structures = []
    in_layering = False
    for line_number, raw_line in enumerate(hyp_paths[0].read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped == "!LAYERING":
            in_layering = True
            continue
        if in_layering and stripped == "!END":
            break
        if in_layering and stripped:
            structure_name = stripped.split(":", 1)[0].strip()
            if structure_name and structure_name not in structures:
                structures.append(structure_name)
    if not structures:
        raise ValueError(f"{hyp_paths[0]}: !LAYERING contains no structures")
    return tuple(structures)


def read_exported_constraint_structures(plan_directory, input_paths):
    common_structures = None
    for input_path in input_paths:
        curve_names = set(load_curves(input_path))
        common_structures = curve_names if common_structures is None else common_structures.intersection(curve_names)
    structures = []
    for structure_name in read_constraint_structures(plan_directory):
        actual_name = STRUCTURE_NAME_ALIASES.get(structure_name, structure_name)
        if actual_name in common_structures and actual_name not in structures:
            structures.append(actual_name)
    if not structures:
        raise ValueError(f"{plan_directory}: no IMRT constraint structures are exported in every DVH input")
    return tuple(structures)


def resolve_structure_names(selected_structures, curves, input_path):
    resolved = []
    for structure_name in selected_structures:
        actual_name = STRUCTURE_NAME_ALIASES.get(structure_name, structure_name)
        if actual_name not in curves:
            actual_name = structure_name
        if actual_name not in curves:
            raise ValueError(f"{input_path}: missing structure: {structure_name}")
        if actual_name not in resolved:
            resolved.append(actual_name)
    return tuple(resolved)


def get_structure_colors(structure_names):
    other_colors = ("#2CA02C", "#1F77B4", "#9467BD", "#8C564B", "#E377C2", "#7F7F7F")
    structure_colors = {
        structure_name: other_colors[iStructure % len(other_colors)]
        for iStructure, structure_name in enumerate(structure_names)
        if structure_name not in ("CTV", "PTV")
    }
    structure_colors["CTV"] = "#FFD700"
    structure_colors["PTV"] = "#E41A1C"
    return structure_colors


def get_maximum_dose(input_paths):
    return max(float(np.max(doses)) for input_path in input_paths for doses, _ in load_curves(input_path).values())


def draw_overlay(input_paths, output_path, selected_structures=None, comparison_input_paths=()):
    first_curves = load_curves(input_paths[0])
    structure_names = resolve_structure_names(selected_structures, first_curves, input_paths[0]) if selected_structures else tuple(first_curves)
    structure_colors = get_structure_colors(structure_names)
    figure, axes = plt.subplots(figsize=(14, 9))

    for iInputPath, input_path in enumerate(input_paths):
        curves = first_curves if iInputPath == 0 else load_curves(input_path)
        missing_structures = set(structure_names).difference(curves)
        if missing_structures:
            raise ValueError(f"{input_path}: missing structures: {', '.join(sorted(missing_structures))}")
        if selected_structures is None:
            unexpected_structures = set(curves).difference(structure_names)
            if unexpected_structures:
                raise ValueError(f"{input_path}: unexpected structures: {', '.join(sorted(unexpected_structures))}")
        for structure_name in structure_names:
            dose, volume = curves[structure_name]
            label = structure_name if iInputPath == 0 else None
            axes.plot(dose, volume, color=structure_colors[structure_name], alpha=0.78, linewidth=1.15, label=label)

    axes.set_title(f"DVH overlay ({len(input_paths)} plans, {len(structure_names)} structures)")
    axes.set_xlabel("Dose (cGy)")
    axes.set_ylabel("Volume (%)")
    maximum_dose = get_maximum_dose(input_paths + comparison_input_paths)
    axes.set_xlim(0, maximum_dose)
    axes.set_ylim(0, 100)
    axes.grid(True, alpha=0.3)
    legend = axes.legend(title="Structure", loc="upper right")
    for legend_line in legend.get_lines():
        legend_line.set_alpha(1)
        legend_line.set_linewidth(2)
    figure.tight_layout()
    figure.savefig(output_path, dpi=200)
    plt.close(figure)


def draw_band(
    input_paths,
    output_path,
    band_structure_names,
    selected_structures,
    reference_input_path,
    reference_label,
    comparison_input_paths=(),
    comparison_reference_input_path=None,
    show_nominal=True,
):
    reference_curves = load_curves(reference_input_path)
    structure_names = resolve_structure_names(selected_structures, reference_curves, reference_input_path)
    band_structure_names = resolve_structure_names(band_structure_names, reference_curves, reference_input_path)
    invalid_band_structures = set(band_structure_names).difference(structure_names)
    if invalid_band_structures:
        raise ValueError(f"band structures are not in IMRT Constraints: {', '.join(sorted(invalid_band_structures))}")

    figure, axes = plt.subplots(figsize=(14, 9))
    structure_colors = get_structure_colors(structure_names)
    for band_structure_name in band_structure_names:
        band_curves = [reference_curves[band_structure_name]]
        for input_path in input_paths:
            input_curves = load_curves(input_path)
            if band_structure_name not in input_curves:
                raise ValueError(f"{input_path}: missing structure: {band_structure_name}")
            band_curves.append(input_curves[band_structure_name])
        dose_axis = np.unique(np.concatenate([dose for dose, _ in band_curves]))
        volumes = np.vstack([np.interp(dose_axis, dose, volume) for dose, volume in band_curves])
        lower_volume = np.min(volumes, axis=0)
        upper_volume = np.max(volumes, axis=0)
        band_color = structure_colors[band_structure_name]
        axes.fill_between(dose_axis, lower_volume, upper_volume, color=band_color, alpha=0.28, label=f"{band_structure_name} band")
        axes.plot(dose_axis, lower_volume, "--", color=band_color, linewidth=1.2)
        axes.plot(dose_axis, upper_volume, "--", color=band_color, linewidth=1.2)
        if show_nominal:
            reference_dose, reference_volume = reference_curves[band_structure_name]
            axes.plot(reference_dose, reference_volume, color=band_color, linewidth=2.0, label=f"{band_structure_name} {reference_label}")
    for structure_name in structure_names:
        if structure_name in band_structure_names:
            continue
        dose, volume = reference_curves[structure_name]
        axes.plot(dose, volume, color=structure_colors[structure_name], alpha=0.9, linewidth=1.6, label=structure_name)
    axes.set_title(f"DVH: {reference_label} with {', '.join(band_structure_names)} bands across source + {len(input_paths)} confirmed comparisons")
    axes.set_xlabel("Dose (cGy)")
    axes.set_ylabel("Volume (%)")
    scale_paths = input_paths + comparison_input_paths + (reference_input_path,)
    if comparison_reference_input_path is not None:
        scale_paths += (comparison_reference_input_path,)
    maximum_dose = get_maximum_dose(scale_paths)
    axes.set_xlim(0, maximum_dose)
    axes.set_ylim(0, 100)
    axes.grid(True, alpha=0.3)
    axes.legend(loc="best")
    figure.tight_layout()
    figure.savefig(output_path, dpi=200)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description="Draw a DVH overlay for scenarios listed in a manifest.")
    parser.add_argument("--input-directory", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--plan-prefix", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", choices=("overlay", "band"), default="overlay")
    parser.add_argument("--structure", action="append", help="Repeat for each structure to draw as a band.")
    parser.add_argument("--constraints-plan", type=Path)
    parser.add_argument("--comparison-plan-prefix")
    parser.add_argument("--reference-plan-name")
    parser.add_argument("--comparison-reference-plan-name")
    parser.add_argument("--no-nominal", action="store_false", dest="show_nominal", default=True, help="Omit the source Plan curves from band plots.")
    args = parser.parse_args()

    try:
        if args.mode == "band" and not args.structure:
            raise ValueError("--structure is required when --mode is band")
        if args.mode == "band" and not args.reference_plan_name:
            raise ValueError("--reference-plan-name is required when --mode is band")
        if args.mode == "overlay" and args.structure and args.constraints_plan:
            raise ValueError("use either --structure or --constraints-plan for overlay")
        if not args.output.parent.is_dir():
            raise FileNotFoundError(f"output directory does not exist: {args.output.parent}")
        input_paths = collect_input_paths(args.input_directory, args.manifest, args.plan_prefix)
        comparison_input_paths = collect_input_paths(args.input_directory, args.manifest, args.comparison_plan_prefix) if args.comparison_plan_prefix else ()
        reference_input_path = collect_plan_path(args.input_directory, args.reference_plan_name) if args.reference_plan_name else None
        comparison_reference_input_path = collect_plan_path(args.input_directory, args.comparison_reference_plan_name) if args.comparison_reference_plan_name else None
        if args.mode == "band":
            if args.constraints_plan is None:
                raise ValueError("--constraints-plan is required when --mode is band")
            selected_structures = read_exported_constraint_structures(args.constraints_plan, input_paths)
            draw_band(
                input_paths,
                args.output,
                args.structure,
                selected_structures,
                reference_input_path,
                args.reference_plan_name,
                comparison_input_paths,
                comparison_reference_input_path,
                args.show_nominal,
            )
        else:
            selected_structures = read_exported_constraint_structures(args.constraints_plan, input_paths) if args.constraints_plan else args.structure
            draw_overlay(input_paths, args.output, selected_structures, comparison_input_paths)
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"drawAllRobustDVH: {error}\n")
    print(f"Generated {args.mode} for {len(input_paths)} files: {args.output}")


if __name__ == "__main__":
    main()
