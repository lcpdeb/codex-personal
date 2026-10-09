import argparse
import csv
import math
import os
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path


COMPARISON_ID = re.compile(r"^C(\d+)$")
REQUIRED_COLUMNS = {
    "comparison_id",
    "selected",
    "include_in_plot",
    "plan_suffix",
    "source_scenario_group",
    "nominal",
    "studyset_id",
    "template_plan",
    "structure_set_id",
    "density_change_percent",
    "density_scale",
    "uncertainty_group",
    "beam_numbers",
    "lr_shift_cm",
    "si_shift_cm",
    "ap_shift_cm",
}


def parse_number(value, field):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field}: value must be finite")
    return number


def read_text_lines(path):
    data = path.read_bytes()
    if b"\r\n" in data:
        if b"\n" in data.replace(b"\r\n", b""):
            raise ValueError(f"{path}: mixed line endings")
        separator = b"\r\n"
    else:
        separator = b"\n"
    trailing_separator = data.endswith(separator)
    lines = data.split(separator)
    if trailing_separator:
        lines.pop()
    return [line.decode("utf-8") for line in lines], separator, trailing_separator


def write_text_lines(path, lines, separator, trailing_separator):
    data = separator.join(line.encode("utf-8") for line in lines)
    if trailing_separator:
        data += separator
    path.write_bytes(data)


def plan_studyset(plan_directory):
    lines, _, _ = read_text_lines(plan_directory / "plan")
    if len(lines) < 7:
        raise ValueError(f"{plan_directory / 'plan'}: plan metadata is incomplete")
    return f"{lines[5]}~{lines[6]}"


def read_manifest(path):
    comparisons = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file, delimiter="\t")
        missing = REQUIRED_COLUMNS.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")
        for line_number, row in enumerate(reader, start=2):
            match = COMPARISON_ID.fullmatch(row["comparison_id"])
            if match is None:
                raise ValueError(f"{path}:{line_number}: invalid comparison_id {row['comparison_id']!r}")
            try:
                selected = int(row["selected"])
                include_in_plot = int(row["include_in_plot"])
            except ValueError as error:
                raise ValueError(f"{path}:{line_number}: selected and include_in_plot must be 0 or 1") from error
            if selected not in (0, 1) or include_in_plot not in (0, 1):
                raise ValueError(f"{path}:{line_number}: selected and include_in_plot must be 0 or 1")
            if include_in_plot and not selected:
                raise ValueError(f"{path}:{line_number}: include_in_plot requires selected=1")
            beams = []
            for value in row["beam_numbers"].split(","):
                try:
                    beam_number = int(value)
                except ValueError as error:
                    raise ValueError(f"{path}:{line_number}: invalid beam number {value!r}") from error
                if beam_number <= 0 or beam_number in beams:
                    raise ValueError(f"{path}:{line_number}: invalid or duplicate beam number {beam_number}")
                beams.append(beam_number)
            if not beams:
                raise ValueError(f"{path}:{line_number}: beam_numbers is empty")
            comparisons[row["comparison_id"]].append(
                {
                    "line": line_number,
                    "selected": selected,
                    "include_in_plot": include_in_plot,
                    "plan_suffix": row["plan_suffix"],
                    "source_group": int(row["source_scenario_group"]),
                    "nominal": int(row["nominal"]),
                    "studyset": row["studyset_id"],
                    "template_plan": row["template_plan"],
                    "structure_set": row["structure_set_id"],
                    "density_change": parse_number(row["density_change_percent"], f"{path}:{line_number}:density_change_percent"),
                    "density_scale": parse_number(row["density_scale"], f"{path}:{line_number}:density_scale"),
                    "uncertainty_group": int(row["uncertainty_group"]),
                    "beams": beams,
                    "lr": parse_number(row["lr_shift_cm"], f"{path}:{line_number}:lr_shift_cm"),
                    "si": parse_number(row["si_shift_cm"], f"{path}:{line_number}:si_shift_cm"),
                    "ap": parse_number(row["ap_shift_cm"], f"{path}:{line_number}:ap_shift_cm"),
                }
            )

    if not comparisons:
        raise ValueError(f"{path}: manifest contains no comparisons")
    ordered_ids = sorted(comparisons, key=lambda comparison_id: int(COMPARISON_ID.fullmatch(comparison_id).group(1)))
    expected_ids = [f"C{index:03d}" for index in range(len(ordered_ids))]
    if ordered_ids != expected_ids:
        raise ValueError(f"{path}: comparison IDs must be continuous from C000")

    selected_suffixes = set()
    selected_comparisons = []
    for comparison_id in ordered_ids:
        rows = comparisons[comparison_id]
        for field in ("selected", "include_in_plot", "plan_suffix", "source_group", "nominal", "studyset", "template_plan", "density_change", "density_scale"):
            values = {row[field] for row in rows}
            if len(values) != 1:
                raise ValueError(f"{path}: {comparison_id} has inconsistent {field}")
        expected_scale = 1.0 + rows[0]["density_change"] / 100.0
        if not math.isclose(rows[0]["density_scale"], expected_scale, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(f"{path}: {comparison_id} has inconsistent density scale")
        uncertainty_groups = [row["uncertainty_group"] for row in rows]
        if len(uncertainty_groups) != len(set(uncertainty_groups)):
            raise ValueError(f"{path}: {comparison_id} has duplicate uncertainty groups")
        all_beams = [beam for row in rows for beam in row["beams"]]
        if len(all_beams) != len(set(all_beams)):
            raise ValueError(f"{path}: {comparison_id} assigns one beam to multiple uncertainty groups")
        if rows[0]["selected"]:
            plan_suffix = rows[0]["plan_suffix"]
            if not plan_suffix or Path(plan_suffix).name != plan_suffix:
                raise ValueError(f"{path}: {comparison_id} has invalid plan_suffix {plan_suffix!r}")
            reference_suffix = rows[0]["studyset"].split("~", 1)[-1]
            if not reference_suffix or not plan_suffix.casefold().startswith(reference_suffix.casefold()):
                raise ValueError(
                    f"{path}: {comparison_id} plan_suffix {plan_suffix!r} must start with "
                    f"the confirmed Reference CT suffix {reference_suffix!r}"
                )
            if plan_suffix.casefold() in selected_suffixes:
                raise ValueError(f"{path}: duplicate selected plan_suffix {plan_suffix!r}")
            selected_suffixes.add(plan_suffix.casefold())
            selected_comparisons.append((comparison_id, rows))
    if not selected_comparisons:
        raise ValueError(f"{path}: no user-confirmed comparisons have selected=1")
    return selected_comparisons


def safe_plan_name(prefix, plan_suffix):
    name = prefix + plan_suffix
    if not prefix or not plan_suffix or Path(name).name != name or name in (".", ".."):
        raise ValueError(f"invalid plan name prefix: {prefix!r}")
    return name


def excluded_template_entry(path):
    name = path.name.casefold()
    return (
        name == "tel.1"
        or name == "robustoptimizationsettings"
        or name.startswith("dose.")
        or ".dose." in name
        or name.endswith(".xlog")
        or name.endswith(".tmp")
    )


def copy_template(template_directory, target_directory):
    target_directory.mkdir()
    for source in template_directory.iterdir():
        if excluded_template_entry(source):
            continue
        destination = target_directory / source.name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)


def update_plan_identity(plan_path, source_name, target_name, expected_studyset):
    lines, separator, trailing_separator = read_text_lines(plan_path)
    if len(lines) < 7 or lines[2] != source_name:
        raise ValueError(f"{plan_path}: unexpected source plan identity")
    actual_studyset = f"{lines[5]}~{lines[6]}"
    if actual_studyset != expected_studyset:
        raise ValueError(f"{plan_path}: StudySet {actual_studyset!r} does not match {expected_studyset!r}")
    lines[2] = target_name
    write_text_lines(plan_path, lines, separator, trailing_separator)


def csv_numbers(line, count, field):
    parts = line.split(",")
    if len(parts) != count:
        raise ValueError(f"{field}: expected {count} fields, found {len(parts)}")
    for value in parts:
        parse_number(value, field)
    return parts


def locate_beam_records(lines, beam_numbers, label, require_matching_drp=True):
    candidates = defaultdict(list)
    expected = set(beam_numbers)
    for i_drp in range(11, len(lines) - 20):
        beam_text = lines[i_drp - 11].strip()
        if not beam_text.isdigit() or int(beam_text) not in expected:
            continue
        try:
            drp = csv_numbers(lines[i_drp], 6, f"{label}:line {i_drp + 1}")
            isocenter = csv_numbers(lines[i_drp + 17], 8, f"{label}:line {i_drp + 18}")
            csv_numbers(lines[i_drp + 19], 2, f"{label}:line {i_drp + 20}")
        except ValueError:
            continue
        if not require_matching_drp or all(
            math.isclose(float(drp[index + 1]), float(isocenter[index]), rel_tol=0.0, abs_tol=1e-9)
            for index in range(3)
        ):
            candidates[int(beam_text)].append(i_drp)

    records = {}
    for beam_number in expected:
        matches = candidates[beam_number]
        if len(matches) != 1:
            raise ValueError(f"{label}: expected one standard TEL record for Beam {beam_number}, found {len(matches)}")
        records[beam_number] = matches[0]
    return records


def format_number(value):
    if value == 0.0:
        return "0"
    return format(value, ".17g")


def shifted_tel(source_path, scenario_rows, geometry_source_path):
    lines, separator, trailing_separator = read_text_lines(source_path)
    beam_shifts = {}
    for row in scenario_rows:
        for beam_number in row["beams"]:
            beam_shifts[beam_number] = (row["lr"], row["si"], row["ap"])
    records = locate_beam_records(lines, beam_shifts, source_path)
    geometry_lines, _, _ = read_text_lines(geometry_source_path)
    geometry_records = locate_beam_records(
        geometry_lines, beam_shifts, geometry_source_path, require_matching_drp=False
    )

    for beam_number, (lr_shift, si_shift, ap_shift) in beam_shifts.items():
        i_drp = records[beam_number]
        drp = lines[i_drp].split(",")
        isocenter = lines[i_drp + 17].split(",")
        geometry_drp = csv_numbers(
            geometry_lines[geometry_records[beam_number]],
            6,
            f"{geometry_source_path}: Beam {beam_number} DRP",
        )
        geometry_isocenter = csv_numbers(
            geometry_lines[geometry_records[beam_number] + 17],
            8,
            f"{geometry_source_path}: Beam {beam_number} isocenter",
        )
        target_drp = (
            float(geometry_drp[1]) + lr_shift * 10.0,
            float(geometry_drp[2]) + si_shift * 10.0,
            float(geometry_drp[3]) + ap_shift * 10.0,
        )
        target_isocenter = (
            float(geometry_isocenter[0]) + lr_shift * 10.0,
            float(geometry_isocenter[1]) + si_shift * 10.0,
            float(geometry_isocenter[2]) + ap_shift * 10.0,
        )
        drp[1:4] = [format_number(value) for value in target_drp]
        isocenter_text = [format_number(value) for value in target_isocenter]
        isocenter[0:3] = isocenter_text
        isocenter[3:6] = isocenter_text
        isocenter[6] = "0"
        lines[i_drp] = ",".join(drp)
        lines[i_drp + 1] = "0,-1"
        lines[i_drp + 2] = ""
        lines[i_drp + 17] = ",".join(isocenter)
        lines[i_drp + 19] = "0,-1"
        lines[i_drp + 20] = ""
    isocenters = []
    for beam_number in sorted(records):
        values = csv_numbers(lines[records[beam_number] + 17], 8, f"{source_path}: Beam {beam_number} isocenter")
        isocenters.append((beam_number, *values[:3]))
    return lines, separator, trailing_separator, isocenters


def stage_plan(template, delivery_source, target_directory, target_name, scenario_rows):
    copy_template(template, target_directory)
    plan_path = target_directory / "plan"
    if not plan_path.is_file():
        raise FileNotFoundError(f"{target_name}: template did not provide a plan file")
    update_plan_identity(plan_path, template.name, target_name, scenario_rows[0]["studyset"])

    source_tel = delivery_source / "tel.1"
    if not source_tel.is_file():
        raise FileNotFoundError(f"delivery source does not contain tel.1: {delivery_source}")
    lines, separator, trailing_separator, isocenters = shifted_tel(source_tel, scenario_rows, template / "tel.1")
    write_text_lines(target_directory / "tel.1", lines, separator, trailing_separator)

    unexpected = [path.name for path in target_directory.iterdir() if excluded_template_entry(path) and path.name.casefold() != "tel.1"]
    if unexpected:
        raise ValueError(f"{target_name}: derived files were copied: {', '.join(sorted(unexpected))}")
    return isocenters


def generate_plans(
    normal_plan,
    robust_plan,
    manifest_path,
    normal_target_prefix=None,
    robust_target_prefix=None,
    robust_only=False,
    overwrite=False,
):
    normal_plan = normal_plan.resolve()
    robust_plan = robust_plan.resolve()
    if not normal_plan.is_dir() or not robust_plan.is_dir():
        raise FileNotFoundError("Normal Plan and Robust Plan directories must both exist")
    plan_root = normal_plan.parent
    if robust_plan.parent != plan_root:
        raise ValueError("Normal Plan and Robust Plan must share the same Plan root")

    normal_target_prefix = normal_plan.name if normal_target_prefix is None else normal_target_prefix
    robust_target_prefix = robust_plan.name if robust_target_prefix is None else robust_target_prefix
    comparisons = read_manifest(manifest_path)
    plan_sources = ((robust_plan, robust_target_prefix),) if robust_only else ((normal_plan, normal_target_prefix), (robust_plan, robust_target_prefix))
    targets = []
    for comparison_id, rows in comparisons:
        for delivery_source, target_prefix in plan_sources:
            template_name = rows[0]["template_plan"]
            template = plan_root / template_name if template_name else delivery_source
            if plan_studyset(template) != rows[0]["studyset"]:
                raise ValueError(f"{comparison_id}: source Plan {template.name} StudySet does not match the manifest")
            target_name = safe_plan_name(target_prefix, rows[0]["plan_suffix"])
            target = plan_root / target_name
            if target.exists() and not overwrite:
                raise FileExistsError(f"target Plan already exists: {target}")
            targets.append((comparison_id, rows, template, delivery_source, target_name, target))

    staging = Path(tempfile.mkdtemp(prefix=".comparison-plan-staging-", dir=plan_root))
    if staging.parent != plan_root or not staging.name.startswith(".comparison-plan-staging-"):
        raise RuntimeError(f"invalid staging directory: {staging}")
    published = []
    backups = []
    cleanup_staging = True
    try:
        staged_targets = []
        isocenter_rows = []
        for comparison_id, rows, template, delivery_source, target_name, target in targets:
            staged = staging / target_name
            isocenters = stage_plan(template, delivery_source, staged, target_name, rows)
            isocenter_rows.extend((comparison_id, target_name, *isocenter) for isocenter in isocenters)
            staged_targets.append((staged, target))
        try:
            for staged, target in staged_targets:
                if target.exists():
                    backup_directory = staging / ".existing"
                    backup_directory.mkdir(exist_ok=True)
                    backup = backup_directory / target.name
                    os.rename(target, backup)
                    backups.append((backup, target))
                os.rename(staged, target)
                published.append(target)
        except Exception as publish_error:
            try:
                for target in reversed(published):
                    os.rename(target, staging / target.name)
                for backup, target in reversed(backups):
                    os.rename(backup, target)
            except Exception as rollback_error:
                cleanup_staging = False
                raise RuntimeError(f"publish failed and rollback failed; staging retained at {staging}") from rollback_error
            raise publish_error
    finally:
        if cleanup_staging and staging.exists():
            shutil.rmtree(staging)
    return len(comparisons), len(targets), isocenter_rows


def main():
    parser = argparse.ArgumentParser(description="Generate paired Normal and Robust Monaco comparison Plan folders.")
    parser.add_argument("--normal-plan", required=True, type=Path)
    parser.add_argument("--normal-target-prefix")
    parser.add_argument("--robust-plan", required=True, type=Path)
    parser.add_argument("--robust-target-prefix")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--robust-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    try:
        comparison_count, plan_count, isocenter_rows = generate_plans(
            args.normal_plan,
            args.robust_plan,
            args.manifest.resolve(),
            args.normal_target_prefix,
            args.robust_target_prefix,
            args.robust_only,
            args.overwrite,
        )
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        parser.exit(1, f"generate_scenario_plans: {error}\n")
    print(f"Generated {plan_count} Plan folders for {comparison_count} comparisons")
    print("comparison_id\tplan_name\tbeam_number\tx_mm\ty_mm\tz_mm")
    for isocenter_row in isocenter_rows:
        print("\t".join(str(value) for value in isocenter_row))


if __name__ == "__main__":
    main()
