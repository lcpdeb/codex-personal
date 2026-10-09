using System;
using System.Collections.Generic;
using System.Configuration;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
using Elekta.MonacoScripting.API;
using Elekta.MonacoScripting.DataType;
using Elekta.MonacoScripting.Log;

namespace MonacoRobustDvhBatch
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Logger.Instance.DeactivateExitErrorHandler();

            var completedPlans = new List<string>();
            try
            {
                var app = MonacoApplication.Instance;
                Require(String.IsNullOrWhiteSpace(app.GetActivePlan()), "An active plan is already loaded. Unload it before starting this batch.");

                var settings = BatchSettings.Load();
                var candidates = BuildTargets(app.GetWorkspaceData(), settings);
                Require(candidates.Count > 0, "The Manifest contains no selected comparison plans found in the Monaco workspace.");

                var patient = app.GetCurrentPatient();
                Require(patient != null && !String.IsNullOrWhiteSpace(patient.Path) && Directory.Exists(patient.Path), "The patient data path is unavailable.");
                var patientFolderName = new DirectoryInfo(patient.Path).Name;
                var patientIdSeparator = patientFolderName.IndexOf('~');
                var patientId = patientIdSeparator >= 0 ? patientFolderName.Substring(patientIdSeparator + 1) : patientFolderName;
                Require(!String.IsNullOrWhiteSpace(patientId), "The patient ID is unavailable.");
                Logger.Instance.Info("DVH output root: " + settings.OutputRootDirectory);
                Directory.CreateDirectory(settings.OutputRootDirectory);

                var targets = new List<TargetPlan>();
                var skippedPlanCount = 0;
                foreach (var candidate in candidates)
                {
                    var hasDose = HasPersistedDose(patient.Path, candidate);
                    if (hasDose)
                    {
                        skippedPlanCount++;
                        Logger.Instance.Info("Skipped plan with persisted dose: " + candidate.DisplayName);
                    }
                    else
                    {
                        var dvhExportPath = GetDvhExportPath(candidate, settings, patientId);
                        Directory.CreateDirectory(Path.GetDirectoryName(dvhExportPath));
                        Require(!File.Exists(dvhExportPath), candidate.DisplayName + ": DVH export file already exists but dose is missing.");
                        targets.Add(candidate);
                    }
                }

                foreach (var target in targets)
                {
                    ProcessPlan(app, target, settings, patientId);
                    completedPlans.Add(target.DisplayName);
                    Logger.Instance.Info("Completed: " + target.DisplayName);
                }

                MessageBox.Show("Completed " + completedPlans.Count + " comparison plans. Skipped " + skippedPlanCount + " plans with persisted dose. See the Monaco Scripting log for the checklist.", "Monaco DVH batch", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            catch (Exception exception)
            {
                Logger.Instance.Error(exception);
                MessageBox.Show("Batch stopped without a compensating action.\r\n\r\n" + exception.Message, "Monaco DVH batch", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private static List<TargetPlan> BuildTargets(WorkspaceData workspace, BatchSettings settings)
        {
            Require(workspace != null, "The Monaco workspace could not be read.");

            var planSuffixes = ReadPlanSuffixes(settings.ManifestPath);
            var expectedPlanNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var planSuffix in planSuffixes)
            {
                expectedPlanNames.Add(settings.RobustPlanName + planSuffix);
                expectedPlanNames.Add(settings.NormalPlanName + planSuffix);
            }

            var plansByName = new Dictionary<string, TargetPlan>(StringComparer.OrdinalIgnoreCase);
            foreach (var study in workspace.Studies)
            {
                foreach (var image in study.Images)
                {
                    foreach (var structureSet in image.StructureSets)
                    {
                        foreach (var plan in structureSet.Plans)
                        {
                            if (expectedPlanNames.Contains(plan.PlanName))
                            {
                                Require(!plansByName.ContainsKey(plan.PlanName), "Duplicate comparison Plan name found: " + plan.PlanName);
                                var target = new TargetPlan(image.ImageName, structureSet.StructureSetName, plan.PlanName);
                                plansByName.Add(plan.PlanName, target);
                            }
                        }
                    }
                }
            }
            var targets = new List<TargetPlan>();
            foreach (var planSuffix in planSuffixes)
            {
                var robustPlanName = settings.RobustPlanName + planSuffix;
                var normalPlanName = settings.NormalPlanName + planSuffix;
                Require(plansByName.ContainsKey(robustPlanName), "Manifest entry is missing from Monaco workspace: " + robustPlanName);
                Require(plansByName.ContainsKey(normalPlanName), "Manifest entry is missing from Monaco workspace: " + normalPlanName);
                targets.Add(plansByName[robustPlanName]);
                targets.Add(plansByName[normalPlanName]);
            }
            return targets;
        }

        private static List<string> ReadPlanSuffixes(string manifestPath)
        {
            Require(File.Exists(manifestPath), "Manifest file does not exist: " + manifestPath);
            var lines = File.ReadAllLines(manifestPath);
            Require(lines.Length > 1, "Manifest contains no comparison rows: " + manifestPath);
            var header = lines[0].Split('\t');
            var iPlanSuffix = Array.FindIndex(header, value => String.Equals(value, "plan_suffix", StringComparison.Ordinal));
            Require(iPlanSuffix >= 0, "Manifest is missing the plan_suffix column: " + manifestPath);
            var iSelected = Array.FindIndex(header, value => String.Equals(value, "selected", StringComparison.Ordinal));
            Require(iSelected >= 0, "Manifest is missing the selected column: " + manifestPath);

            var planSuffixes = new List<string>();
            var uniqueSuffixes = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (var iLine = 1; iLine < lines.Length; iLine++)
            {
                var fields = lines[iLine].Split('\t');
                Require(fields.Length > iSelected && (fields[iSelected] == "0" || fields[iSelected] == "1"), manifestPath + ": invalid selected value at line " + (iLine + 1));
                if (fields[iSelected] == "0")
                {
                    continue;
                }
                Require(fields.Length > iPlanSuffix && !String.IsNullOrWhiteSpace(fields[iPlanSuffix]), manifestPath + ": invalid plan_suffix at line " + (iLine + 1));
                if (uniqueSuffixes.Add(fields[iPlanSuffix]))
                {
                    planSuffixes.Add(fields[iPlanSuffix]);
                }
            }
            Require(planSuffixes.Count > 0, "Manifest contains no user-confirmed comparisons with selected=1.");

            return planSuffixes;
        }

        private static void ProcessPlan(MonacoApplication app, TargetPlan target, BatchSettings settings, string patientId)
        {
            Require(app.LoadPlan(target.PlanName), target.DisplayName + ": failed to load into Planning.");
            Require(String.Equals(app.GetActivePlan(), target.PlanName, StringComparison.Ordinal), target.DisplayName + ": active-plan identity is unclear.");
            Require(ExecuteCalculationFromTopLevelButton(app), target.DisplayName + ": calculation did not reach Final dose calculation complete.");
            Require(app.SetRelativeMode(false), target.DisplayName + ": failed to disable Isodoses Relative Mode.");
            Require(app.SetDoseUnit(Elekta.MonacoScripting.DataType.Unit.DoseUnit.cGy), target.DisplayName + ": failed to set the Isodoses dose unit to cGy.");
            Require(app.DVHExport(GetDvhExportPath(target, settings, patientId)), target.DisplayName + ": cumulative DVH export failed.");
            Require(app.SavePatient(), target.DisplayName + ": save did not complete.");
            Require(app.UnLoadAll(MonacoApplication.SaveDlgOptions.DoNothing), target.DisplayName + ": unload failed.");
            Require(String.IsNullOrWhiteSpace(app.GetActivePlan()), target.DisplayName + ": plan remains active after unload.");
        }

        private static bool ExecuteCalculationFromTopLevelButton(MonacoApplication app)
        {
            var getRibbonBarCtrl = typeof(MonacoApplication).GetMethod("GetRibbonBarCtrl", BindingFlags.Instance | BindingFlags.NonPublic);
            Require(getRibbonBarCtrl != null, "The installed MonacoScripting DLL does not expose its ribbon controller.");
            var ribbonBarCtrl = getRibbonBarCtrl.Invoke(app, null);
            Require(ribbonBarCtrl != null, "The Monaco ribbon controller is unavailable.");

            var getPlanningRibbonBar = ribbonBarCtrl.GetType().GetMethod("GetPlanningRibbonBar", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            Require(getPlanningRibbonBar != null, "The installed MonacoScripting DLL does not expose its Planning ribbon controller.");
            var planningRibbonBar = getPlanningRibbonBar.Invoke(ribbonBarCtrl, null);
            Require(planningRibbonBar != null, "The Planning ribbon controller is unavailable.");

            var basePlanningRibbonBar = planningRibbonBar.GetType().BaseType;
            Require(basePlanningRibbonBar != null, "The installed MonacoScripting DLL has an unsupported Planning ribbon type.");
            var calculationStatus = basePlanningRibbonBar.GetNestedType("CalculationStatus", BindingFlags.Public | BindingFlags.NonPublic);
            Require(calculationStatus != null, "The installed MonacoScripting DLL has no calculation status type.");
            var complete = Enum.Parse(calculationStatus, "Complete");
            var parameterTypes = new[] { typeof(string), typeof(int), typeof(int), calculationStatus, typeof(string) };
            var calculateDose = basePlanningRibbonBar.GetMethod("CalculateDose", BindingFlags.Instance | BindingFlags.NonPublic, null, parameterTypes, null);
            Require(calculateDose != null, "The installed MonacoScripting DLL has no compatible direct Calculate entry.");

            Logger.Instance.Info("Starting dose calculation from the top-level Calculate button.");
            var result = calculateDose.Invoke(planningRibbonBar, new object[] { "btnCalculate", 60 * 60 * 1000, 5 * 1000, complete, String.Empty });
            Require(result is bool, "The direct Calculate entry returned an unexpected result.");
            return (bool)result;
        }

        private static string GetDvhExportPath(TargetPlan target, BatchSettings settings, string patientId)
        {
            return Path.Combine(settings.OutputRootDirectory, patientId + "_" + target.PlanName + "_DVH_1.txt");
        }

        private static bool HasPersistedDose(string patientFolder, TargetPlan target)
        {
            var planFolder = Path.Combine(patientFolder, "plan", target.PlanName);
            Require(Directory.Exists(planFolder), target.DisplayName + ": plan folder is unavailable.");
            return File.Exists(Path.Combine(planFolder, "dose.1"));
        }

        private static void Require(bool condition, string message)
        {
            if (!condition)
            {
                throw new InvalidOperationException(message);
            }
        }

        private sealed class BatchSettings
        {
            internal string ManifestPath { get; private set; }
            internal string OutputRootDirectory { get; private set; }
            internal string NormalPlanName { get; private set; }
            internal string RobustPlanName { get; private set; }

            internal static BatchSettings Load()
            {
                var settings = new BatchSettings();
                settings.ManifestPath = RequiredPath("ManifestPath");
                settings.OutputRootDirectory = RequiredPath("OutputRootDirectory");
                settings.NormalPlanName = RequiredValue("NormalPlanName");
                settings.RobustPlanName = RequiredValue("RobustPlanName");
                return settings;
            }

            private static string RequiredPath(string key)
            {
                return Path.GetFullPath(RequiredValue(key));
            }

            private static string RequiredValue(string key)
            {
                var value = ConfigurationManager.AppSettings[key];
                Require(!String.IsNullOrWhiteSpace(value), "Missing appSetting: " + key);
                return value;
            }
        }

        private sealed class TargetPlan
        {
            internal TargetPlan(string imageName, string structureSetName, string planName)
            {
                ImageName = imageName;
                StructureSetName = structureSetName;
                PlanName = planName;
            }

            internal string ImageName { get; private set; }
            internal string StructureSetName { get; private set; }
            internal string PlanName { get; private set; }
            internal string DisplayName => ImageName + " / " + StructureSetName + " / " + PlanName;
        }
    }
}
