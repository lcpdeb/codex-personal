import argparse
import csv
import struct
from pathlib import Path


MAX_COUNT = 1_000_000
HEADER = (
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
)


class BinaryReader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def read(self, size, field):
        end = self.offset + size
        if end > len(self.data):
            raise ValueError(f"{field}: unexpected end of file at byte {self.offset}")
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def int32(self, field):
        return struct.unpack("<i", self.read(4, field))[0]

    def count(self, field):
        value = self.int32(field)
        if value < 0 or value > MAX_COUNT:
            raise ValueError(f"{field}: invalid count {value}")
        return value

    def float64(self, field):
        return struct.unpack("<d", self.read(8, field))[0]

    def boolean(self, field):
        return struct.unpack("<?", self.read(1, field))[0]

    def string(self, field):
        length = self.count(f"{field}.length")
        return self.read(length, field).decode("utf-8")

    def delimited_string(self, field):
        end = self.data.find(b"*", self.offset)
        if end < 0 or end - self.offset > MAX_COUNT:
            raise ValueError(f"{field}: missing delimiter")
        value = self.data[self.offset:end].decode("utf-8")
        self.offset = end + 1
        return value


def reference_studyset(plan_directory):
    plan_path = plan_directory / "plan"
    lines = plan_path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 7 or not lines[6].strip():
        raise ValueError(f"{plan_path}: reference StudySet is unavailable")

    display_name = lines[6].strip()
    patient_directory = plan_directory.parent.parent
    matches = [
        path.name
        for path in patient_directory.iterdir()
        if path.is_dir() and path.name.split("~", 1)[-1].casefold() == display_name.casefold()
    ]
    if len(matches) != 1:
        raise ValueError(f"{plan_path}: expected one StudySet matching {display_name!r}, found {len(matches)}")
    return matches[0]


def plan_beam_numbers(plan_directory):
    tel_path = plan_directory / "tel.1"
    lines = tel_path.read_text(encoding="utf-8").splitlines()
    beam_numbers = []
    for i_line in range(11, len(lines) - 20):
        beam_text = lines[i_line - 11].strip()
        if (
            lines[i_line - 1].strip() == "DRP"
            and beam_text.isdigit()
            and len(lines[i_line].split(",")) == 6
            and len(lines[i_line + 17].split(",")) == 8
        ):
            beam_number = int(beam_text)
            if beam_number not in beam_numbers:
                beam_numbers.append(beam_number)
    if not beam_numbers:
        raise ValueError(f"{tel_path}: no Beam records found")
    return beam_numbers


def resolve_instance_reference(plan_directory, instance, settings_path):
    candidate_plan_name = instance["candidate_plan"]
    if candidate_plan_name and Path(candidate_plan_name).name != candidate_plan_name:
        raise ValueError(f"{settings_path}: invalid candidate Plan {candidate_plan_name!r}")
    template_directory = plan_directory.parent / candidate_plan_name if candidate_plan_name else plan_directory
    if not template_directory.is_dir():
        raise FileNotFoundError(f"Template Plan directory does not exist: {template_directory}")
    reference_studyset_id = reference_studyset(template_directory)
    if instance["studyset"] and instance["studyset"].casefold() != reference_studyset_id.casefold():
        raise ValueError(
            f"{settings_path}: template Plan {template_directory.name!r} references {reference_studyset_id!r}, "
            f"but Robust Settings contains {instance['studyset']!r}"
        )
    return candidate_plan_name, reference_studyset_id


def parse_settings(settings_path):
    reader = BinaryReader(settings_path.read_bytes())
    reader.int32("version")
    reader.int32("combination_mode")
    reader.int32("error_calculation_point_mode")
    reader.int32("shift_direction")
    reader.int32("uncertainty_mode")
    reader.boolean("apply_to_all")

    uncertainty_groups = []
    for i_group in range(reader.count("uncertainty_group_count")):
        reader.boolean(f"uncertainty_group[{i_group}].is_first")
        for axis in ("x", "y", "z"):
            reader.float64(f"uncertainty_group[{i_group}].isocenter_{axis}")
        beam_numbers = [
            reader.int32(f"uncertainty_group[{i_group}].beam[{i_beam}]")
            for i_beam in range(reader.count(f"uncertainty_group[{i_group}].beam_count"))
        ]
        for i_description in range(reader.count(f"uncertainty_group[{i_group}].description_count")):
            reader.delimited_string(f"uncertainty_group[{i_group}].description[{i_description}]")
        for direction in ("superior", "inferior", "left", "right", "posterior", "anterior"):
            reader.float64(f"uncertainty_group[{i_group}].{direction}")
        uncertainty_groups.append(beam_numbers)

    reader.boolean("skip_direction_enabled")
    reader.int32("skip_direction")
    reader.boolean("only_apply_to_single_isocenter")
    reader.float64("density_setting")

    scenario_groups = []
    for i_scenario_group in range(reader.count("scenario_group_count")):
        instance_count = reader.count(f"scenario_group[{i_scenario_group}].instance_count")
        if instance_count == 0:
            scenario_groups.append({"source": i_scenario_group, "selected": False, "density": 0.0, "instances": []})
            continue

        selected = reader.boolean(f"scenario_group[{i_scenario_group}].selected")
        studyset_selected = reader.boolean(f"scenario_group[{i_scenario_group}].studyset_selected")
        density = reader.float64(f"scenario_group[{i_scenario_group}].density")
        instances = []
        for i_instance in range(instance_count):
            instances.append(
                {
                    "lr": reader.float64(f"scenario_group[{i_scenario_group}].instance[{i_instance}].lr"),
                    "si": reader.float64(f"scenario_group[{i_scenario_group}].instance[{i_instance}].si"),
                    "ap": reader.float64(f"scenario_group[{i_scenario_group}].instance[{i_instance}].ap"),
                    "candidate_plan": reader.string(f"scenario_group[{i_scenario_group}].instance[{i_instance}].candidate_plan"),
                    "studyset": reader.string(f"scenario_group[{i_scenario_group}].instance[{i_instance}].studyset"),
                    "structure_set": reader.string(f"scenario_group[{i_scenario_group}].instance[{i_instance}].structure_set"),
                }
            )
        scenario_groups.append({"source": i_scenario_group, "selected": selected and studyset_selected, "density": density, "instances": instances})

    if reader.offset != len(reader.data):
        raise ValueError(f"{settings_path}: {len(reader.data) - reader.offset} unread bytes remain")
    return uncertainty_groups, scenario_groups


def manifest_rows(plan_directory):
    settings_path = plan_directory / "robustOptimizationSettings"
    if not settings_path.is_file():
        raise FileNotFoundError(f"Robust Settings file does not exist: {settings_path}")

    uncertainty_groups, scenario_groups = parse_settings(settings_path)
    selected_groups = [group for group in scenario_groups if group["selected"]]
    if not selected_groups:
        raise ValueError(f"{settings_path}: no selected comparison entries")

    all_beam_numbers = ",".join(str(number) for number in plan_beam_numbers(plan_directory))
    rows = []
    density_changes = []
    comparison_count = 0
    for group in selected_groups:
        if uncertainty_groups and len(group["instances"]) == len(uncertainty_groups):
            comparison_instances = ((group["instances"], True),)
        elif len(group["instances"]) == 1:
            comparison_instances = ((group["instances"], False),)
        elif not uncertainty_groups:
            comparison_instances = tuple(((instance,), False) for instance in group["instances"])
        else:
            raise ValueError(
                f"{settings_path}: scenario group {group['source']} has {len(group['instances'])} instances; "
                f"expected one candidate instance or {len(uncertainty_groups)} uncertainty-group instances"
            )

        for instances, use_uncertainty_groups in comparison_instances:
            comparison_id = f"C{comparison_count:03d}"
            comparison_count += 1
            nominal = int(
                group["density"] == 0.0
                and all(instance["lr"] == 0.0 and instance["si"] == 0.0 and instance["ap"] == 0.0 for instance in instances)
            )
            density_scale = 1.0 + group["density"] / 100.0
            references = [resolve_instance_reference(plan_directory, instance, settings_path) for instance in instances]
            if len({reference_studyset_id.casefold() for _, reference_studyset_id in references}) != 1:
                raise ValueError(f"{settings_path}: {comparison_id} has inconsistent Reference CT StudySets")
            if len({candidate_plan_name.casefold() for candidate_plan_name, _ in references}) != 1:
                raise ValueError(f"{settings_path}: {comparison_id} has inconsistent template Plans")
            studyset_id = references[0][1]
            template_plan = references[0][0]

            if group["density"] != 0.0:
                density_changes.append((comparison_id, studyset_id, group["density"], density_scale))
            for i_instance, instance in enumerate(instances):
                beam_numbers = (
                    ",".join(str(beam_number) for beam_number in uncertainty_groups[i_instance])
                    if use_uncertainty_groups
                    else all_beam_numbers
                )
                rows.append(
                    (
                        comparison_id,
                        0,
                        0,
                        "",
                        group["source"],
                        nominal,
                        studyset_id,
                        template_plan,
                        instance["structure_set"],
                        group["density"],
                        density_scale,
                        i_instance,
                        beam_numbers,
                        instance["lr"],
                        instance["si"],
                        instance["ap"],
                    )
                )
    return rows, comparison_count, density_changes


def write_manifest(output_path, rows):
    output_file = output_path.open("x", encoding="utf-8", newline="")
    try:
        with output_file:
            writer = csv.writer(output_file, delimiter="\t", lineterminator="\n")
            writer.writerow(HEADER)
            writer.writerows(rows)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description="Generate an unconfirmed comparison inventory from a Monaco Robust Plan.")
    parser.add_argument("--robust-plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        if not args.robust_plan.is_dir():
            raise FileNotFoundError(f"Robust Plan directory does not exist: {args.robust_plan}")
        if not args.output.parent.is_dir():
            raise FileNotFoundError(f"Output directory does not exist: {args.output.parent}")
        rows, comparison_count, density_changes = manifest_rows(args.robust_plan)
        write_manifest(args.output, rows)
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"generate_scenario_manifest: {error}\n")

    print(f"Generated unconfirmed inventory {args.output}: {comparison_count} comparisons, {len(rows)} rows")
    if density_changes:
        print("Non-zero density comparisons:")
        print("comparison_id\tstudyset_id\tdensity_change_percent\tdensity_scale")
        for density_change in density_changes:
            print("\t".join(str(value) for value in density_change))
    else:
        print("Density: all discovered comparisons are 0%; no density patch is required")


if __name__ == "__main__":
    main()
