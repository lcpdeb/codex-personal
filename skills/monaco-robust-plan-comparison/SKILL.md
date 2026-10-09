---
name: monaco-robust-plan-comparison
description: 对比已完成的 Monaco Normal 与 Robust Plan，按用户确认的 StudySet 与 scenario 单独或组合生成成对计划、DVH 和共享坐标图。
---

# Monaco Robust Plan 与 Normal Plan 对比工作流

每完成一步，报告实际结果并等待用户确认后再继续。

StudySet 与 scenario 是两个独立维度。一次比较可以只包含其中一个维度，也可以包含两者组合；skill 不根据 Robust Settings 的布局自动决定比较模式。脚本先生成未确认清单，用户确认具体组合后才执行。

## 步骤 1：确认并核对源 Plan

向用户确认 Normal Plan 和 Robust Plan 的完整目录与准确名称。前提是两份 Plan 已完成优化并保存。只询问缺失的信息，不得猜测患者、StudySet 或 Plan；信息不全时暂停。

信息齐全后，在磁盘上确认两份源 Plan 存在、属于用户指定的比较对象且优化结果已保存。Robust Plan 必须包含 `robustOptimizationSettings`。核对失败时停止，不修改患者或 Plan 数据。

读取并展示 Normal/Robust 源 Plan 的实际 Reference CT StudySet。不得从 Plan 名或目录名推断 Reference CT；candidate 模板 Plan 的 Reference CT 在步骤 2 清单中展示并确认。

## 步骤 2：生成并确认比较清单

从 Robust Plan 的 `robustOptimizationSettings` 生成候选组合清单。脚本只盘点数据，不决定用户要比较哪些组合：

每个 `comparison_id` 表示一个可选组合，可以同时包含特定 Reference CT StudySet 和特定几何/density scenario。初始 `selected=0`、`include_in_plot=0`、`plan_suffix` 为空，因而不能直接生成 Plan。

```powershell
$skillRoot = "<Skill 安装目录>"
$robustPlanDir = "<Robust Plan 完整目录>"
$runDirectory = Join-Path $robustPlanDir "RobustComparison"
$robustPlanName = Split-Path -Leaf $robustPlanDir
$manifest = Join-Path $runDirectory "comparison_manifest_${robustPlanName}.tsv"
$manifestScript = Join-Path $skillRoot "scripts\generate_scenario_manifest.py"

if (Test-Path -LiteralPath $runDirectory) {
    throw "目标目录已存在：$runDirectory"
}
New-Item -ItemType Directory -Path $runDirectory -ErrorAction Stop | Out-Null

py -3 $manifestScript --robust-plan $robustPlanDir --output $manifest
```

`RobustComparison` 是默认批次目录；用户可以指定其他目录。目录或目标 Manifest 已存在时停止，不覆盖。

向用户展示每个 `comparison_id` 的 Reference CT StudySet、模板 Plan、Structure Set、Beam 分组、位移和 density。由用户明确确认：

- 选择哪些 StudySet、scenario 以及哪些 StudySet × scenario 组合；
- 每个所选组合的唯一 `plan_suffix`：以确认后的 Reference CT StudySet 名称后缀为基础；同一 Reference CT 有多个 scenario 时，由用户确认附加的区分后缀；
- 哪些所选组合设置 `include_in_plot=1`；
- band 图使用哪个源 Plan 作为实线。

用户确认后才修改清单：所选组合的所有行统一设置 `selected=1` 并填写 `plan_suffix`，需要绘图的组合设置 `include_in_plot=1`。未选组合保持初始值。后缀重复、同一组合内字段不一致或 `include_in_plot=1` 但 `selected=0` 时停止。

如果用户要求的 StudySet × scenario 组合没有出现在清单中，停止并报告缺失组合；不得自行拼接位移、density、Beam 分组或模板 Plan。

## 步骤 3：生成成对比较 Plan

目标 Plan 已存在时停止，不覆盖。Normal 与 Robust 派生 Plan 前缀均由用户确认。

目标 Plan 名是 `<Normal 或 Robust 前缀><plan_suffix>`。`plan_suffix` 必须以步骤 2 确认的 Reference CT StudySet 名称后缀开头；额外的 scenario 区分部分只使用用户确认的值。

清单中的 `template_plan` 提供目标 Reference CT StudySet、Structure Set、DRP 和 isocenter；Normal/Robust 源 Plan 分别提供其优化 Beam。模板 Plan 的 DRP 与 isocenter 可以因 StudySet 配准而不同，必须分别保留，不能要求两者相等。所有派生 Plan 均排除旧剂量、XLOG 和 `robustOptimizationSettings`。

```powershell
$skillRoot = "<Skill 安装目录>"
$normalPlanDir = "<Normal Plan 完整目录>"
$robustPlanDir = "<Robust Plan 完整目录>"
$runDirectory = Join-Path $robustPlanDir "RobustComparison"
$robustPlanName = Split-Path -Leaf $robustPlanDir
$manifest = Join-Path $runDirectory "comparison_manifest_${robustPlanName}.tsv"
$normalPlanPrefix = "<Normal 派生 Plan 前缀>"
$robustPlanPrefix = "<Robust 派生 Plan 前缀>"
$planScript = Join-Path $skillRoot "scripts\generate_scenario_plans.py"

py -3 $planScript --normal-plan $normalPlanDir --normal-target-prefix $normalPlanPrefix --robust-plan $robustPlanDir --robust-target-prefix $robustPlanPrefix --manifest $manifest
```

展示脚本输出的每个 Plan、StudySet、每个 Beam 的 X/Y/Z，等待用户确认。

## 步骤 4：Calculate 并导出 DVH TXT

用户确认的组合包含非零 density 时，报告对应组合、StudySet、density 百分比和 scale，然后停止并申请处理授权。

全部 density 为 0 时，DVH 输出目录默认使用本批 `RobustComparison`；用户可以指定其他目录。已有目标 DVH 时不覆盖：对应 Plan 已有持久化剂量则直接进入绘图，尚无剂量则停止并让用户处理旧文件。然后准备本批 Monaco Scripting 项目：

```powershell
$skillRoot = "<Skill 安装目录>"
$robustPlanDir = "<Robust Plan 完整目录>"
$runDirectory = Join-Path $robustPlanDir "RobustComparison"
$batchProjectDirectory = Join-Path $runDirectory "MonacoRobustDvhBatch"

if (Test-Path -LiteralPath $batchProjectDirectory) {
    throw "目标目录已存在：$batchProjectDirectory"
}
Copy-Item -LiteralPath (Join-Path $skillRoot "scripts\MonacoRobustDvhBatch") -Destination $batchProjectDirectory -Recurse
```

填写 `$batchProjectDirectory\App.config`：

- `ManifestPath`：步骤 2 的 Manifest。
- `OutputRootDirectory`：本批 DVH 输出目录，默认是 `RobustComparison`。程序会将患者 ID 写入每个输出文件名。
- `NormalPlanName`：Normal 派生 Plan 前缀，不一定等于 Normal 源 Plan 名。
- `RobustPlanName`：Robust 派生 Plan 前缀。

经用户明确授权后编译 Release。编译成功后，确认 `MonacoRobustDvhBatch.exe` 存在，并报告其父目录的实际绝对路径。

运行由用户完成：

1. 用户在 Monaco 打开目标患者，并确保没有 Plan 处于加载状态。
2. 用户打开 Monaco 左上角菜单中的 `Manage Scripts`，从上一步输出的目录添加 `MonacoRobustDvhBatch.exe`。
3. 用户在 Monaco 中手动运行脚本。Codex 不从 PowerShell 或外部程序启动 EXE。

DVH TXT 输出位置：

```text
<DvhOutput>\<patientID>_<完整PlanName>_DVH_1.txt
```

band 图还需要两份用户确认的源 Plan DVH TXT。每张图使用对应源 Plan 加清单中所有 `include_in_plot=1` 的派生 Plan，共同计算阴影 band 和上下边界；源 Plan 同时绘制为绿色实线，因此必定位于 band 内。源 Plan DVH 缺失时，先在 Monaco 中导出，不能用任意比较成员代替。

进入步骤 5 前抽查：

- Dose 数值必须与 Monaco 显示的 Max Dose 同量级；约 0–200 且步长 0.1 表示相对剂量，停止绘图并重新导出。

## 步骤 5：生成 DVH 图

按用户要求生成 overlay 和/或 band；目标结构必须由用户确认。绘图脚本只读取 `selected=1` 且 `include_in_plot=1` 的清单条目。若需要完整的 Normal/Robust overlay 与 band 集合，执行以下四次调用。

```powershell
# 本批输入
$skillRoot = "<Skill 安装目录>"
$patientId = "<patientID>"
$normalPlanPrefix = "<Normal 派生 Plan 前缀>"
$robustPlanPrefix = "<Robust 派生 Plan 前缀>"
$targetStructure = "<Target Structure，例如 CTV>"
$normalPlanDir = "<Normal 源 Plan 完整目录>"
$robustPlanDir = "<Robust 源 Plan 完整目录>"
$normalSourcePlanName = Split-Path -Leaf $normalPlanDir
$robustSourcePlanName = Split-Path -Leaf $robustPlanDir
$runDirectory = Join-Path $robustPlanDir "RobustComparison"
$outputRoot = $runDirectory

# 派生路径
$robustPlanName = Split-Path -Leaf $robustPlanDir
$manifest = Join-Path $runDirectory "comparison_manifest_${robustPlanName}.tsv"
$plotScript = Join-Path $skillRoot "scripts\drawAllRobustDVH.py"
$dvhDir = $outputRoot

py -3 $plotScript --mode overlay --input-directory $dvhDir --manifest $manifest --plan-prefix $normalPlanPrefix --comparison-plan-prefix $robustPlanPrefix --constraints-plan $normalPlanDir --output (Join-Path $dvhDir "${patientId}_${normalPlanPrefix}_DVH_overlay.png")

py -3 $plotScript --mode overlay --input-directory $dvhDir --manifest $manifest --plan-prefix $robustPlanPrefix --comparison-plan-prefix $normalPlanPrefix --constraints-plan $robustPlanDir --output (Join-Path $dvhDir "${patientId}_${robustPlanPrefix}_DVH_overlay.png")

py -3 $plotScript --mode band --input-directory $dvhDir --manifest $manifest --plan-prefix $normalPlanPrefix --comparison-plan-prefix $robustPlanPrefix --reference-plan-name $normalSourcePlanName --comparison-reference-plan-name $robustSourcePlanName --constraints-plan $normalPlanDir --structure $targetStructure --output (Join-Path $dvhDir "${patientId}_${normalPlanPrefix}_${targetStructure}_DVH_band.png")

py -3 $plotScript --mode band --input-directory $dvhDir --manifest $manifest --plan-prefix $robustPlanPrefix --comparison-plan-prefix $normalPlanPrefix --reference-plan-name $robustSourcePlanName --comparison-reference-plan-name $normalSourcePlanName --constraints-plan $robustPlanDir --structure $targetStructure --output (Join-Path $dvhDir "${patientId}_${robustPlanPrefix}_${targetStructure}_DVH_band.png")
```

任一 DVH TXT 更新后必须重新执行对应绘图调用。完成后确认用户要求的 PNG 非空、坐标轴、图例、曲线和 band 清晰。
